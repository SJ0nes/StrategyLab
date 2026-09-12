"""Trading 212 venue adapter. DEMO environment only, for this project.

WARNING: the T212 order endpoint is NOT idempotent in beta - a repeated request
can create a duplicate order. NEVER blind-retry. On timeout, query order state
before any resend.

Rate limits: account summary 1 request per 5 seconds, historical 6 requests per
minute. The API key is masked in every error trace; an exception that escapes
with a credential in it is a credential in a log file.

TRANSPORT IS INJECTABLE AND THAT IS HOW IT IS TESTED. ``tests/test_t212.py``
drives every method through an ``httpx.MockTransport`` that records what was
sent and answers with canned JSON, so no test makes a network call and the only
thing untested is the vendor itself: whether the endpoints below still answer
in the shapes this module expects is verified by the first real run, not by the
suite. The endpoint paths and payload keys are those of the public T212 API v0
as documented at the time of writing.

LOCAL STATE IS ONE FILE: ``state/positions.json``, the cache of what the venue
last reported holding. :meth:`T212Venue.reconcile` is the only writer, and the
venue is always authoritative - a cache that disagrees with its source is
simply wrong and is overwritten.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from strategylab.core.types import Side
from strategylab.execution.venue import (
    Balance,
    Order,
    OrderId,
    Position,
    ReconcileReport,
)

DEMO_BASE_URL = "https://demo.trading212.com/api/v0"
LIVE_BASE_URL = "https://live.trading212.com/api/v0"

ACCOUNT_SUMMARY_MIN_INTERVAL_S: float = 5.0
HISTORICAL_MAX_REQUESTS_PER_MIN: int = 6

#: Where the local positions cache lives, relative to the working directory.
DEFAULT_POSITIONS_PATH = Path("state/positions.json")

#: What replaces the key in any message that would otherwise carry it.
MASK = "***"

#: How many seconds an HTTP call may take before it is a timeout. A timeout on
#: the order endpoint is the one failure this module refuses to resolve on the
#: caller's behalf, so the number is visible rather than buried.
DEFAULT_TIMEOUT_S: float = 10.0

_ENVIRONMENTS = {"demo": DEMO_BASE_URL, "live": LIVE_BASE_URL}


class T212Error(Exception):
    """Raised on any Trading 212 API failure.

    The message never contains the API key. Adapters mask credentials before
    the exception is constructed, not at the point it is printed, because there
    is no guarantee about who prints it.
    """


class T212Venue:
    """A :class:`strategylab.execution.venue.Venue` backed by Trading 212.

    The demo and live environments differ ONLY in base URL, which is precisely
    why the environment is read from configuration and never defaulted: a
    single wrong string is the whole distance between paper and real money.

    SYMBOLOGY IS THE CALLER'S. The protocol names its execution instrument in
    vendor-neutral form (``VUAG.L``) and T212 names the same thing its own way;
    ``symbol_map`` translates one to the other and defaults to the identity, so
    an unmapped symbol is sent as written and refused by the venue rather than
    silently traded as something else. The mapping for this project's
    instrument is unverified: no test may call the instruments endpoint that
    would confirm it, and ``LIMITATIONS.md`` says so.
    """

    def __init__(
        self,
        api_key: str,
        environment: str = "demo",
        *,
        transport: httpx.BaseTransport | None = None,
        positions_path: Path = DEFAULT_POSITIONS_PATH,
        symbol_map: dict[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        """Construct an adapter for one T212 environment.

        Args:
            api_key: T212 API key, read from the environment, never hardcoded
                and never logged.
            environment: Either ``demo`` or ``live``. No default to ``live``
                exists anywhere in this system.
            transport: HTTP transport, injected by tests as an
                ``httpx.MockTransport``. ``None`` uses a real one.
            positions_path: The local positions cache, the one file this module
                writes.
            symbol_map: Protocol symbol to T212 ticker, identity when absent.
            clock: Monotonic seconds, for the account-summary rate limit.
            sleep: How to wait out that limit. Injected so a test can assert the
                wait without incurring it.
            timeout_s: Seconds before an HTTP call is a timeout.

        Raises:
            ValueError: If ``environment`` is not ``demo`` or ``live``, or
                ``api_key`` is empty.
        """
        if environment not in _ENVIRONMENTS:
            raise ValueError(
                f"T212 environment must be 'demo' or 'live', got {environment!r}; there is no "
                "default and no third option"
            )
        if not api_key or not api_key.strip():
            raise ValueError("T212 api_key is empty; set T212_API_KEY in the environment")
        self._key = api_key
        self.environment = environment
        self.base_url = _ENVIRONMENTS[environment]
        self._positions_path = positions_path
        self._symbol_map = dict(symbol_map or {})
        self._clock = clock
        self._sleep = sleep
        self._last_summary_at: float | None = None
        self._currency: str | None = None
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": api_key, "Accept": "application/json"},
            timeout=timeout_s,
            transport=transport,
        )

    def _mask(self, text: str) -> str:
        """Return ``text`` with every occurrence of the key replaced by :data:`MASK`.

        Args:
            text: Any string that might carry the credential.

        Returns:
            The masked string.
        """
        return text.replace(self._key, MASK)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Make one request and return its decoded JSON, or raise :class:`T212Error`.

        NO RETRY OF ANY KIND, on any method. A timeout on a GET could be retried
        safely; a timeout on the order POST cannot, and one code path that
        retries is one code path away from retrying the wrong thing.

        Args:
            method: HTTP method.
            path: Path under the environment's base URL.
            **kwargs: Passed to ``httpx.Client.request``.

        Returns:
            The response body decoded from JSON, or ``None`` for an empty body.

        Raises:
            T212Error: On a timeout, a transport failure, a non-2xx status, or a
                body that is not JSON - with the key masked in every case.
        """
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise T212Error(
                self._mask(
                    f"{method} {path} timed out after {self._client.timeout.read}s; NOT "
                    "retried. If this was an order, query the venue's open orders before "
                    "deciding whether it exists"
                )
            ) from exc
        except httpx.HTTPError as exc:
            raise T212Error(self._mask(f"{method} {path} failed: {exc}")) from exc
        if response.status_code // 100 != 2:
            raise T212Error(
                self._mask(
                    f"{method} {path} returned HTTP {response.status_code}: {response.text[:200]}"
                )
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise T212Error(
                self._mask(f"{method} {path} returned a body that is not JSON")
            ) from exc

    def _ticker(self, symbol: str) -> str:
        """Translate a protocol symbol to the venue's ticker.

        Args:
            symbol: The symbol as the protocol names it.

        Returns:
            The venue ticker, or ``symbol`` itself when unmapped.
        """
        return self._symbol_map.get(symbol, symbol)

    def place_order(self, order: Order) -> OrderId:
        """Submit an order and return the venue's identifier for it.

        Does NOT retry on timeout. The endpoint is not idempotent, so a resend
        can create a second position; on timeout the caller must query order
        state by ``order.client_ref`` and decide from what it finds.

        T212 v0 ACCEPTS NO CLIENT REFERENCE, so ``order.client_ref`` is not
        sent: it is carried in this adapter's error message on a timeout and in
        the caller's log, and resolution by query means matching ticker,
        quantity and time against the venue's open orders. That is weaker than
        the interface's docstring implies and ``LIMITATIONS.md`` says so.

        Direction is carried by the SIGN of the quantity, which is how the v0
        market-order endpoint expresses it: a sell is a negative quantity.

        Args:
            order: The order to submit, already approved by the risk layer.

        Returns:
            The venue's identifier for the submitted order.

        Raises:
            T212Error: On any API failure, with the key masked.
            ValueError: If ``order.quantity`` is not positive - direction
                belongs to ``side``, and a signed quantity here would be sent
                with its sign flipped.
        """
        if not order.quantity > 0:
            raise ValueError(
                f"order quantity must be positive, got {order.quantity}; the side carries direction"
            )
        signed = order.quantity if order.side is Side.BUY else -order.quantity
        try:
            body = self._request(
                "POST",
                "/equity/orders/market",
                json={"ticker": self._ticker(order.ticker), "quantity": signed},
            )
        except T212Error as exc:
            raise T212Error(f"{exc} (client_ref {order.client_ref})") from exc
        if not isinstance(body, dict) or "id" not in body:
            raise T212Error(
                f"order accepted but the response carried no id (client_ref {order.client_ref})"
            )
        return OrderId(str(body["id"]))

    def cancel(self, order_id: OrderId) -> None:
        """Cancel a working order.

        Args:
            order_id: Venue identifier of the order to cancel.

        Returns:
            None.

        Raises:
            T212Error: On any API failure, with the key masked.
        """
        self._request("DELETE", f"/equity/orders/{order_id}")

    def get_positions(self) -> list[Position]:
        """Return every position the venue reports holding.

        Returns:
            The current positions, empty when flat. ``market_value`` is
            ``quantity * currentPrice`` as the venue reports both, in the
            instrument's currency - which is the account currency only when the
            two coincide, and this adapter does not convert.

        Raises:
            T212Error: On any API failure, with the key masked, or on a payload
                missing a field this module reads.
        """
        body = self._request("GET", "/equity/portfolio")
        if not isinstance(body, list):
            raise T212Error("portfolio endpoint did not return a list")
        positions = []
        for raw in body:
            try:
                quantity = float(raw["quantity"])
                positions.append(
                    Position(
                        ticker=str(raw["ticker"]),
                        quantity=quantity,
                        average_price=float(raw["averagePrice"]),
                        market_value=quantity * float(raw["currentPrice"]),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise T212Error(f"portfolio entry is missing or mistypes a field: {exc}") from exc
        return positions

    def get_balance(self) -> Balance:
        """Return the account's cash and equity as the venue reports them.

        Subject to the account-summary rate limit of one request per five
        seconds: a second call within the interval sleeps out the remainder
        rather than being refused by the venue. The currency comes from the
        account-info endpoint and is fetched once, then remembered.

        Returns:
            The current balance. ``cash`` is the venue's ``free`` figure and
            ``equity`` its ``total``.

        Raises:
            T212Error: On any API failure, with the key masked.
        """
        now = self._clock()
        if self._last_summary_at is not None:
            remaining = ACCOUNT_SUMMARY_MIN_INTERVAL_S - (now - self._last_summary_at)
            if remaining > 0:
                self._sleep(remaining)
        if self._currency is None:
            info = self._request("GET", "/equity/account/info")
            if not isinstance(info, dict) or "currencyCode" not in info:
                raise T212Error("account info carried no currencyCode")
            self._currency = str(info["currencyCode"])
        cash = self._request("GET", "/equity/account/cash")
        self._last_summary_at = self._clock()
        try:
            return Balance(
                currency=self._currency,
                cash=float(cash["free"]),
                equity=float(cash["total"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise T212Error(f"account cash payload is missing or mistypes a field: {exc}") from exc

    def _read_local(self) -> tuple[Position, ...]:
        """Read the local positions cache, empty if absent or unreadable.

        Unreadable is treated as empty on purpose: the cache is about to be
        overwritten by the venue's answer, and refusing to reconcile because the
        cache is corrupt would leave it corrupt.

        Returns:
            The cached positions.
        """
        try:
            raw = json.loads(self._positions_path.read_text(encoding="utf-8"))
            return tuple(Position(**entry) for entry in raw)
        except (OSError, ValueError, TypeError):
            return ()

    def reconcile(self) -> ReconcileReport:
        """Ask T212 what it actually holds and repair local state to match.

        Runs on every startup. A discrepancy is a ticker held on one side and
        not the other, or held on both at different quantities. The venue's
        answer is written to the cache whenever anything differed.

        Returns:
            What differed and whether local state was repaired.

        Raises:
            T212Error: On any API failure, with the key masked.
            OSError: If the cache cannot be written.
        """
        venue = tuple(self.get_positions())
        local = self._read_local()
        by_venue = {p.ticker: p for p in venue}
        by_local = {p.ticker: p for p in local}
        discrepancies = []
        for ticker in sorted(by_venue.keys() | by_local.keys()):
            held = by_venue.get(ticker)
            cached = by_local.get(ticker)
            if held is None and cached is not None:
                discrepancies.append(
                    f"{ticker}: local cache holds {cached.quantity} units, venue holds none"
                )
            elif cached is None and held is not None:
                discrepancies.append(
                    f"{ticker}: venue holds {held.quantity} units, local cache holds none"
                )
            elif held is not None and cached is not None and held.quantity != cached.quantity:
                discrepancies.append(
                    f"{ticker}: venue holds {held.quantity} units, local cache says "
                    f"{cached.quantity}"
                )
        repaired = bool(discrepancies)
        if repaired:
            self._positions_path.parent.mkdir(parents=True, exist_ok=True)
            self._positions_path.write_text(
                json.dumps([p.__dict__ for p in venue], indent=2) + "\n", encoding="utf-8"
            )
        return ReconcileReport(
            checked_at=datetime.now(UTC),
            venue_positions=venue,
            local_positions=local,
            discrepancies=tuple(discrepancies),
            repaired=repaired,
        )
