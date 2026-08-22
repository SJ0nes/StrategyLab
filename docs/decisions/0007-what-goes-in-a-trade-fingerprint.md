# 0007 — What goes in a trade fingerprint, and the two cases that needed deciding

## Context

`search/plateau.py`'s `is_degenerate` compares trade SETS rather than trade
counts, because two cells that each took five trades on different days are not
the same strategy and a count would call them identical. It reads a
`trade_fingerprint` string per cell and fixes only the property that string must
have:

> two cells share a fingerprint exactly when they executed the same trades

It then hands the content to the search loop, and names two cases that need a
deliberate answer rather than a default:

> a cell that took no trades, and a cell whose backtest raised. Giving both the
> empty string would make a region of failures look like a region of agreement.

Nothing produced a fingerprint until `search/loop.py` existed, so `is_degenerate`
was built, tested against hand-made frames, and unusable. This record is the
answer that made it usable.

## Decision

Three kinds of fingerprint, mutually exclusive by prefix.

**A cell that traded — `trades:<sha256>`.** The SHA-256 of the cell's ordered
fill sequence, each fill rendered as its side, mid price, executed price and
quantity.

**A cell that took no trades — `no-trades`, one shared value.** Cells that
executed nothing did the same nothing, so a region of them SHOULD read as
degenerate. That is not a concession; it is the correct answer. Parameters that
produce no trades at all are not doing anything, which is precisely what the
degeneracy check exists to say out loud.

**A cell whose backtest raised — `failed:<cell>`, unique per cell.** The
parameters themselves, rendered in sorted order.

## Why the fills and not the trade dates

A fingerprint built from entry and exit timestamps would read better in a file,
and `Fill` does not carry one. Recovering the dates means re-deriving the
position transitions out of the decision log — a second implementation of
something the engine already returned, and one that could disagree with it. The
fills are what the engine actually produced.

The identity holds in both directions on real bars, with one soft edge worth
stating:

- **Same trade dates implies same fills.** The engine is deterministic, and every
  fill price and quantity follows from the bars and the cash trajectory.
- **Same fills implies same dates only up to a price collision** — two different
  bars whose opens are bit-identical floats, sitting at the same position in an
  otherwise identical sequence. Vanishingly unlikely on price data. Not
  impossible, and not guarded against.

A position opened and never closed contributes its entry fill, so two cells
differing only in whether the last position was still open are different cells.
That is right: they ended holding different things.

## Why the two special cases get opposite treatments

They look symmetrical and are not, and getting this backwards is the trap
`plateau.py` warned about.

A neighbourhood of **no-trade** cells is a true finding: the parameters in that
region are inert, and flagging the plateau as degenerate is the honest report.

A neighbourhood of **failed** cells is not a finding about parameters at all —
nothing ran. If those cells shared a fingerprint they would compare equal, the
neighbourhood would report as degenerate, and the reader would be told "these
parameters are doing nothing" when the truth is "these cells produced no
result". The two readings call for opposite responses, so they must not produce
the same string.

Hence: one shared value for the first, one distinct value per cell for the
second.

## Consequences

**`is_degenerate` is usable, and `LIMITATIONS.md`'s entry saying it is not is
now stale.** It said the check was "built, tested against hand-made frames, and
unusable until the search loop decides what goes in that column". The loop has
decided.

**The soft edge is real and unguarded.** A price collision would merge two cells
that traded on different days. Nothing detects it, and no test could reasonably
construct it from recorded bars. It is recorded here rather than defended
against.

**A fingerprint is not portable across engine changes.** It hashes executed
prices, so changing the cost model, the fill convention or the position sizing
changes every fingerprint in the grid. That is correct for its purpose — it is
only ever compared within a single search — but it means fingerprints from two
different runs must never be compared, and nothing in the code stops someone
doing so.

**Two of the graveyard's five rejection reasons are still unwritten by anything.**
`degenerate_plateau`, because `CLAUDE.md` says degenerate plateaus are flagged
rather than rejected and the loop obeys that. `below_noise_ceiling`, because the
ceiling — built since this record was written, see `docs/decisions/0008` — is
reported rather than enforced: `select_candidate` never receives it, so nothing
rejects a cell for sitting inside the noise. Both gaps are deliberate; the
vocabulary is not missing a branch.
