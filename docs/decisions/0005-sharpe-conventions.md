# 0005 — The Sharpe conventions, written down outside the code that uses them

## Context

`backtest/metrics.py` fixes five conventions in computing a Sharpe ratio. An
audit pointed out that every one of them was asserted in exactly two places: the
module's own docstring, and the test that checks the module. That is not a
specification — it is an implementation and its mirror. Nothing a reviewer could
consult said what the number was supposed to mean, so nothing could have called
the implementation wrong.

This matters more for Sharpe than for most things. It is the project's
`validation.primary_metric`, it carries the pass decision at
`min_sharpe + pass_margin = 1.20`, and — as the module's own docstring says — a
Sharpe computed slightly wrong is always plausible. Several of the choices below
change the number by tens of percent without changing how it looks.

## Decision

**1. The standard deviation is the SAMPLE one, dividing by `n - 1`.**

The population form divides by `n` and reports a Sharpe too high by
`sqrt(n / (n - 1))`. Over a year of daily bars that is about 0.2%; over a
20-observation fold it is 2.6%. The inflation is largest exactly where the
sample is smallest, which is where a marginal candidate gets decided.

A third form exists and is easy to write by accident: dividing by a hardcoded
`1`. On a two-observation series it is indistinguishable from the sample form,
because `n - 1` *is* 1 there. `tests/test_metrics.py` therefore pins the divisor
on a three-observation series, where the three candidates give `2.0`, `sqrt(2)`
and `sqrt(6)` — three separable closed forms.

**2. Annualisation multiplies by `sqrt(periods_per_year)`, not by
`periods_per_year`.**

The mean of a return series scales with the number of periods; its standard
deviation scales with the square root of it. The ratio therefore scales with the
square root. Multiplying by the period count instead inflates a daily Sharpe by
`sqrt(252) ≈ 15.87`.

**3. `periods_per_year` defaults to 252, and that is a property of the DATA, not
a universal constant.**

252 is the approximate count of US equity trading sessions in a year, which is
what `protocol.yaml`'s `instruments.research: SPY` on daily bars produces. It is
a default and not a hard-coded value precisely so that a weekly or intraday
series can pass its own. Any change to the bar frequency must pass the matching
count or every Sharpe in the run is wrong by a constant factor.

**4. Returns are SIMPLE per-period fractions — not log returns, and not
percentages.**

Nothing in the code can detect a violation, which is why it is written down here
rather than guarded. A series of percentages scales the mean and the standard
deviation by 100 each, so the *ratio is identical* and the error is invisible in
the Sharpe; it surfaces only in the equity curve, much later, as a return a
hundred times too large. Log returns would be a smaller and subtler error: they
are close to simple returns for small moves and diverge as moves get larger, so
the Sharpe would be slightly wrong in a way that scales with volatility.

**5. A zero-variance series returns `0.0`, not infinity and not an error.**

A constant return series has no risk-adjusted return to speak of. Reporting
infinity would let a strategy that never trades — whose returns are a column of
zeros — post the best Sharpe in the grid and win a search. Returning zero puts it
where it belongs, and the case has a test.

**6. The risk-free rate is zero, and not configurable.**

Stated already in the module docstring and repeated here because it belongs with
the rest. A correct non-zero rate would have to be a time series over the same
window; a single scalar for it is a worse approximation than omitting it. The
consequence is disclosed in `LIMITATIONS.md`: the 2005–2024 window spans rates
from 5% to 0% and back, so Sharpe figures are not comparable across it.

## Consequences

None of these changes the code. The point is that a reviewer can now disagree
with a convention without having to reverse-engineer it from the implementation,
and that a future contributor changing one has something to change other than
the thing they are changing.

The undocumented-contract finding that prompted this applies elsewhere in the
repository and is not fully closed by this record. It is the class of defect
where an implementation and its test agree with each other and with nothing
else, and mutation testing cannot detect it: breaking the code still breaks the
test, so the pair looks healthy while both are wrong together.
