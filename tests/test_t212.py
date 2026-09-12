"""Tests for the Trading 212 adapter, through a mock transport.

NO TEST HERE TOUCHES THE NETWORK. Every request goes to an ``httpx.MockTransport``
that records what the adapter sent and answers with canned JSON. So what is
tested is the adapter's side of the contract - paths, headers, payload shape,
sign of a sell, masking of the key, refusal to retry - and NOT the vendor's:
whether T212 still answers these paths in these shapes is a fact the first real
run establishes, and ``LIMITATIONS.md`` says so.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from strategylab.core.types import Side
from strategylab.execution.t212 import (
    ACCOUNT_SUMMARY_MIN_INTERVAL_S,
    DEMO_BASE_URL,
    LIVE_BASE_URL,
    MASK,
    T212Error,
    T212Venue,
)
from strategylab.execution.venue import Order, OrderId, OrderType, Position

KEY = "secret-key-0123456789"


class Recorder:
    """A transport that records requests and answers from a script."""

    def __init__(self, answers: dict[tuple[str, str], Any]) -> None:
        """Script answers keyed by (method, path); an Exception value is raised."""
        self.requests: list[httpx.Request] = []
        self.answers = answers

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Record the request and answer it from the script."""
        self.requests.append(request)
        key = (request.method, request.url.path)
        if key not in self.answers:
            return httpx.Response(404, json={"error": f"unscripted {key}"})
        answer = self.answers[key]
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        return httpx.Response(status, json=body) if body is not None else httpx.Response(status)

    def transport(self) -> httpx.MockTransport:
        """The transport to inject."""
        return httpx.MockTransport(self.handler)


def _venue(recorder: Recorder, tmp_path: Path, **kwargs: Any) -> T212Venue:
    sleeps: list[float] = []
    kwargs.setdefault("sleep", sleeps.append)
    venue = T212Venue(
        KEY,
        "demo",
        transport=recorder.transport(),
        positions_path=tmp_path / "positions.json",
        **kwargs,
    )
    venue.sleeps = sleeps  # type: ignore[attr-defined]
    return venue


def _order(side: Side = Side.BUY, quantity: float = 3.5) -> Order:
    return Order(
        ticker="VUAG.L",
        side=side,
        quantity=quantity,
        order_type=OrderType.MARKET,
        client_ref="ref-1",
    )


# ---------------------------------------------------------------------------
# construction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "   ", "prod", "DEMO", "sandbox"])
def test_a_wrong_environment_is_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="'demo' or 'live'"):
        T212Venue(KEY, bad)


@pytest.mark.parametrize("bad", ["", "   "])
def test_an_empty_key_is_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="api_key is empty"):
        T212Venue(bad, "demo")


def test_environments_differ_only_in_base_url(tmp_path: Path) -> None:
    demo = T212Venue(KEY, "demo", transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    live = T212Venue(KEY, "live", transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert demo.base_url == DEMO_BASE_URL
    assert live.base_url == LIVE_BASE_URL
    assert demo.base_url != live.base_url


def test_the_default_environment_is_demo() -> None:
    assert (
        T212Venue(KEY, transport=httpx.MockTransport(lambda r: httpx.Response(200))).environment
        == "demo"
    )


# ---------------------------------------------------------------------------
# place_order
# ---------------------------------------------------------------------------


def test_a_buy_is_posted_as_a_positive_quantity_with_the_key_in_the_header(tmp_path: Path) -> None:
    rec = Recorder({("POST", "/api/v0/equity/orders/market"): (200, {"id": 4242})})
    venue = _venue(rec, tmp_path)
    assert venue.place_order(_order(Side.BUY, 3.5)) == OrderId("4242")
    (request,) = rec.requests
    assert request.headers["Authorization"] == KEY
    assert json.loads(request.content) == {"ticker": "VUAG.L", "quantity": 3.5}


def test_a_sell_is_posted_as_a_negative_quantity(tmp_path: Path) -> None:
    rec = Recorder({("POST", "/api/v0/equity/orders/market"): (200, {"id": 1})})
    _venue(rec, tmp_path).place_order(_order(Side.SELL, 2.0))
    assert json.loads(rec.requests[0].content)["quantity"] == -2.0


def test_the_symbol_map_translates_the_ticker(tmp_path: Path) -> None:
    rec = Recorder({("POST", "/api/v0/equity/orders/market"): (200, {"id": 1})})
    _venue(rec, tmp_path, symbol_map={"VUAG.L": "VUAGl_EQ"}).place_order(_order())
    assert json.loads(rec.requests[0].content)["ticker"] == "VUAGl_EQ"


def test_an_unmapped_symbol_is_sent_as_written(tmp_path: Path) -> None:
    rec = Recorder({("POST", "/api/v0/equity/orders/market"): (200, {"id": 1})})
    _venue(rec, tmp_path, symbol_map={"OTHER": "X"}).place_order(_order())
    assert json.loads(rec.requests[0].content)["ticker"] == "VUAG.L"


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_a_non_positive_quantity_is_refused_before_any_request(tmp_path: Path, bad: float) -> None:
    rec = Recorder({})
    with pytest.raises(ValueError, match="must be positive"):
        _venue(rec, tmp_path).place_order(_order(Side.SELL, bad))
    assert rec.requests == []


def test_a_timeout_is_not_retried_and_names_the_client_ref(tmp_path: Path) -> None:
    rec = Recorder({("POST", "/api/v0/equity/orders/market"): httpx.ReadTimeout("slow")})
    with pytest.raises(T212Error, match="NOT retried") as info:
        _venue(rec, tmp_path).place_order(_order())
    assert len(rec.requests) == 1
    assert "ref-1" in str(info.value)


def test_a_rejected_order_raises_with_the_status(tmp_path: Path) -> None:
    rec = Recorder({("POST", "/api/v0/equity/orders/market"): (400, {"code": "InsufficientFunds"})})
    with pytest.raises(T212Error, match="HTTP 400"):
        _venue(rec, tmp_path).place_order(_order())


def test_an_accepted_order_without_an_id_is_an_error(tmp_path: Path) -> None:
    rec = Recorder({("POST", "/api/v0/equity/orders/market"): (200, {"status": "ok"})})
    with pytest.raises(T212Error, match="no id"):
        _venue(rec, tmp_path).place_order(_order())


# ---------------------------------------------------------------------------
# masking - the key must never appear in an error
# ---------------------------------------------------------------------------


def test_the_key_is_masked_in_an_error_body(tmp_path: Path) -> None:
    rec = Recorder({("POST", "/api/v0/equity/orders/market"): (401, {"echo": KEY})})
    with pytest.raises(T212Error) as info:
        _venue(rec, tmp_path).place_order(_order())
    assert KEY not in str(info.value)
    assert MASK in str(info.value)


def test_the_key_is_masked_even_when_it_straddles_the_truncation_cut(tmp_path: Path) -> None:
    # The body is truncated to 200 characters for the message. Masking after the
    # cut would print the key's first characters whenever the key began inside
    # the window and ended outside it; masking happens first.
    padding = "x" * (200 - 8)
    rec = Recorder({("POST", "/api/v0/equity/orders/market"): (500, {"pad": padding + KEY})})
    with pytest.raises(T212Error) as info:
        _venue(rec, tmp_path).place_order(_order())
    assert KEY[:6] not in str(info.value)


def test_the_key_is_masked_in_a_transport_error(tmp_path: Path) -> None:
    rec = Recorder({("GET", "/api/v0/equity/portfolio"): httpx.ConnectError(f"refused for {KEY}")})
    with pytest.raises(T212Error) as info:
        _venue(rec, tmp_path).get_positions()
    assert KEY not in str(info.value)


# ---------------------------------------------------------------------------
# cancel, positions, balance
# ---------------------------------------------------------------------------


def test_cancel_deletes_the_order(tmp_path: Path) -> None:
    rec = Recorder({("DELETE", "/api/v0/equity/orders/77"): (200, None)})
    _venue(rec, tmp_path).cancel(OrderId("77"))
    assert rec.requests[0].method == "DELETE"


def test_positions_are_read_with_market_value_derived(tmp_path: Path) -> None:
    rec = Recorder(
        {
            ("GET", "/api/v0/equity/portfolio"): (
                200,
                [
                    {
                        "ticker": "VUAGl_EQ",
                        "quantity": 4.0,
                        "averagePrice": 80.0,
                        "currentPrice": 90.5,
                    }
                ],
            )
        }
    )
    (position,) = _venue(rec, tmp_path).get_positions()
    assert position == Position(
        ticker="VUAGl_EQ", quantity=4.0, average_price=80.0, market_value=362.0
    )


def test_a_flat_account_has_no_positions(tmp_path: Path) -> None:
    rec = Recorder({("GET", "/api/v0/equity/portfolio"): (200, [])})
    assert _venue(rec, tmp_path).get_positions() == []


def test_a_position_missing_a_field_is_an_error(tmp_path: Path) -> None:
    rec = Recorder({("GET", "/api/v0/equity/portfolio"): (200, [{"ticker": "X", "quantity": 1.0}])})
    with pytest.raises(T212Error, match="missing or mistypes"):
        _venue(rec, tmp_path).get_positions()


def _balance_answers() -> dict[tuple[str, str], Any]:
    return {
        ("GET", "/api/v0/equity/account/info"): (200, {"currencyCode": "GBP", "id": 1}),
        ("GET", "/api/v0/equity/account/cash"): (200, {"free": 1500.25, "total": 9800.0}),
    }


def test_balance_reads_cash_and_equity_in_the_account_currency(tmp_path: Path) -> None:
    balance = _venue(Recorder(_balance_answers()), tmp_path).get_balance()
    assert (balance.currency, balance.cash, balance.equity) == ("GBP", 1500.25, 9800.0)


def test_the_currency_is_fetched_once(tmp_path: Path) -> None:
    rec = Recorder(_balance_answers())
    venue = _venue(rec, tmp_path, clock=lambda: 1000.0)
    venue.get_balance()
    venue.get_balance()
    assert [r.url.path for r in rec.requests].count("/api/v0/equity/account/info") == 1


def test_a_second_summary_within_five_seconds_waits_out_the_remainder(tmp_path: Path) -> None:
    # The clock is read before and after each request. The first request runs
    # from 100 to 101, so its completion is recorded at 101; the second arrives
    # at 103, 2.0s later, and must wait the other 3.0s. Recording the START
    # instead would wait only 2.0s - which is why the before and after ticks
    # differ here, where an earlier version had them equal and could not tell.
    ticks = iter([100.0, 101.0, 103.0, 104.0])
    venue = _venue(Recorder(_balance_answers()), tmp_path, clock=lambda: next(ticks))
    venue.get_balance()
    venue.get_balance()
    assert ACCOUNT_SUMMARY_MIN_INTERVAL_S == 5.0
    assert venue.sleeps == [pytest.approx(3.0)]  # type: ignore[attr-defined]


def test_a_second_summary_after_the_interval_does_not_wait(tmp_path: Path) -> None:
    ticks = iter([100.0, 100.0, 106.0, 106.0])
    venue = _venue(Recorder(_balance_answers()), tmp_path, clock=lambda: next(ticks))
    venue.get_balance()
    venue.get_balance()
    assert venue.sleeps == []  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# reconcile - the venue is authoritative
# ---------------------------------------------------------------------------


def _portfolio(*entries: tuple[str, float]) -> dict[tuple[str, str], Any]:
    return {
        ("GET", "/api/v0/equity/portfolio"): (
            200,
            [
                {"ticker": t, "quantity": q, "averagePrice": 1.0, "currentPrice": 1.0}
                for t, q in entries
            ],
        )
    }


def test_reconcile_with_no_cache_and_a_flat_venue_finds_nothing(tmp_path: Path) -> None:
    report = _venue(Recorder(_portfolio()), tmp_path).reconcile()
    assert report.discrepancies == ()
    assert report.repaired is False
    assert not (tmp_path / "positions.json").exists()


def test_reconcile_writes_the_venue_truth_when_the_cache_disagrees(tmp_path: Path) -> None:
    (tmp_path / "positions.json").write_text(
        json.dumps(
            [{"ticker": "VUAGl_EQ", "quantity": 2.0, "average_price": 1.0, "market_value": 2.0}]
        )
    )
    report = _venue(Recorder(_portfolio(("VUAGl_EQ", 5.0))), tmp_path).reconcile()
    assert report.repaired is True
    assert report.discrepancies == ("VUAGl_EQ: venue holds 5.0 units, local cache says 2.0",)
    assert json.loads((tmp_path / "positions.json").read_text())[0]["quantity"] == 5.0


def test_reconcile_reports_a_position_the_cache_never_knew(tmp_path: Path) -> None:
    report = _venue(Recorder(_portfolio(("NEW", 1.0))), tmp_path).reconcile()
    assert report.discrepancies == ("NEW: venue holds 1.0 units, local cache holds none",)


def test_reconcile_reports_a_position_the_venue_no_longer_holds(tmp_path: Path) -> None:
    (tmp_path / "positions.json").write_text(
        json.dumps([{"ticker": "GONE", "quantity": 1.0, "average_price": 1.0, "market_value": 1.0}])
    )
    report = _venue(Recorder(_portfolio()), tmp_path).reconcile()
    assert report.discrepancies == ("GONE: local cache holds 1.0 units, venue holds none",)
    assert json.loads((tmp_path / "positions.json").read_text()) == []


def test_reconcile_leaves_a_matching_cache_untouched(tmp_path: Path) -> None:
    text = json.dumps([{"ticker": "A", "quantity": 1.0, "average_price": 1.0, "market_value": 1.0}])
    (tmp_path / "positions.json").write_text(text)
    report = _venue(Recorder(_portfolio(("A", 1.0))), tmp_path).reconcile()
    assert report.repaired is False
    assert (tmp_path / "positions.json").read_text() == text


def test_a_corrupt_cache_is_treated_as_empty_and_overwritten(tmp_path: Path) -> None:
    (tmp_path / "positions.json").write_text("{not json")
    report = _venue(Recorder(_portfolio(("A", 1.0))), tmp_path).reconcile()
    assert report.local_positions == ()
    assert report.repaired is True


def test_a_corrupt_cache_is_replaced_even_when_the_venue_is_flat(tmp_path: Path) -> None:
    # Corrupt plus flat used to mean "empty and matched": nothing differed, so
    # nothing was written, and the corrupt file stayed. Unreadable is now a
    # discrepancy in its own right.
    (tmp_path / "positions.json").write_text("{not json")
    report = _venue(Recorder(_portfolio()), tmp_path).reconcile()
    assert report.repaired is True
    assert any("unreadable" in line for line in report.discrepancies)
    assert json.loads((tmp_path / "positions.json").read_text()) == []


def test_positions_come_back_under_the_protocols_symbol(tmp_path: Path) -> None:
    # The map is applied on the way out AND reversed on the way in, so the
    # worker's one-position rule compares like with like. Before the reverse
    # map, a non-identity map made every held position invisible to it.
    rec = Recorder(_portfolio(("VUAGl_EQ", 2.0), ("OTHER_EQ", 1.0)))
    positions = _venue(rec, tmp_path, symbol_map={"VUAG.L": "VUAGl_EQ"}).get_positions()
    assert [p.ticker for p in positions] == ["VUAG.L", "OTHER_EQ"]


def test_the_key_is_masked_in_a_mistyped_payload(tmp_path: Path) -> None:
    rec = Recorder({("GET", "/api/v0/equity/portfolio"): (200, [{"ticker": "X", "quantity": KEY}])})
    with pytest.raises(T212Error) as info:
        _venue(rec, tmp_path).get_positions()
    assert KEY not in str(info.value)
    assert info.value.__cause__ is None


def test_reconcile_stamps_utc(tmp_path: Path) -> None:
    report = _venue(Recorder(_portfolio()), tmp_path).reconcile()
    assert report.checked_at.utcoffset() is not None
    assert report.checked_at.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
