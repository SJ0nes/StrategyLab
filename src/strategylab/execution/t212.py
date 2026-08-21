"""Trading 212 venue adapter. DEMO environment only, for this project.

WARNING: the T212 order endpoint is NOT idempotent in beta - a repeated request
can create a duplicate order. NEVER blind-retry. On timeout, query order state
before any resend.

Rate limits: account summary 1 request per 5 seconds, historical 6 requests per
minute. The API key is masked in every error trace; an exception that escapes
with a credential in it is a credential in a log file.
"""

from __future__ import annotations

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
    """

    def __init__(self, api_key: str, environment: str = "demo") -> None:
        """Construct an adapter for one T212 environment.

        Args:
            api_key: T212 API key, read from the environment, never hardcoded
                and never logged.
            environment: Either ``demo`` or ``live``. No default to ``live``
                exists anywhere in this system.

        Raises:
            ValueError: If ``environment`` is not ``demo`` or ``live``, or
                ``api_key`` is empty.
        """
        raise NotImplementedError

    def place_order(self, order: Order) -> OrderId:
        """Submit an order and return the venue's identifier for it.

        Does NOT retry on timeout. The endpoint is not idempotent, so a resend
        can create a second position; on timeout the caller must query order
        state by ``order.client_ref`` and decide from what it finds.

        Args:
            order: The order to submit, already approved by the risk layer.

        Returns:
            The venue's identifier for the submitted order.

        Raises:
            T212Error: On any API failure, with the key masked.
        """
        raise NotImplementedError

    def cancel(self, order_id: OrderId) -> None:
        """Cancel a working order.

        Args:
            order_id: Venue identifier of the order to cancel.

        Returns:
            None.

        Raises:
            T212Error: On any API failure, with the key masked.
        """
        raise NotImplementedError

    def get_positions(self) -> list[Position]:
        """Return every position the venue reports holding.

        Returns:
            The current positions, empty when flat.

        Raises:
            T212Error: On any API failure, with the key masked.
        """
        raise NotImplementedError

    def get_balance(self) -> Balance:
        """Return the account's cash and equity as the venue reports them.

        Subject to the account-summary rate limit of one request per five
        seconds.

        Returns:
            The current balance.

        Raises:
            T212Error: On any API failure, with the key masked.
        """
        raise NotImplementedError

    def reconcile(self) -> ReconcileReport:
        """Ask T212 what it actually holds and repair local state to match.

        Runs on every startup.

        Returns:
            What differed and whether local state was repaired.

        Raises:
            T212Error: On any API failure, with the key masked.
        """
        raise NotImplementedError
