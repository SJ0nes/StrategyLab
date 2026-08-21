# 0001 — Exhaustive grid search over iterative tree search

## Context

The strategy config declares three searched parameters — `fast`, `slow`, and
`trend` — with coarse ranges that expand to 90 combinations. Something has to
walk that space and nominate a candidate.

The obvious alternative to walking all of it is an iterative search: hill
climbing, a tree search, Bayesian optimisation, or any of the methods that spend
their evaluations where results look promising. These are strictly more
efficient at finding a maximum, and efficiency is the wrong objective here.

## Decision

Walk the entire grid, exhaustively, every time.

## Consequences

**A tree only visits the path it walked. A grid reveals the whole surface — and
the surface is the actual output of this project.**

The question this pipeline asks is not "which parameters performed best". It is
"is the good region broad or is it a spike". Those are answered by the shape of
the results across the parameter space, not by its maximum. An iterative search
returns a point and the trail it took to get there; it cannot say whether the
neighbours of that point also work, because it never evaluated most of them. That
makes plateau scoring, edge-winner detection, and degeneracy checking impossible
to compute — and those three are the entire mechanism by which this project
distinguishes a robust setting from a lucky one.

**Exhaustive search is also what makes the noise ceiling meaningful.** The
ceiling is the best-in-grid result achieved on surrogate data with no edge in it,
and it is a property of *how many combinations were tried*. With a fixed grid,
that number is known exactly and is identical between the real run and the
surrogate runs. With an adaptive search, the effective number of hypotheses
tested depends on the path taken and is not knowable, so there is no honest
multiplicity correction to apply.

**The cost is affordable and bounded.** 90 combinations over roughly 3,500 daily
bars is a small computation, and it is fixed in advance rather than dependent on
convergence. The real constraint is not runtime but data: the config's own
comment notes that this much history supports three or four searched parameters
at most. Grid size grows multiplicatively with parameters, and so does the number
of ways to fit noise — so the pressure the grid puts on keeping the parameter
count small is a feature, not a limitation to be engineered around.

**Accepted downside.** Coarse steps can step over a narrow optimum. This is
deliberate: a maximum that a coarse grid steps over is one that would not survive
the neighbourhood-median scoring anyway. If the winner sits on a range boundary,
that is detected and rejected as an edge winner, and the range is widened and
searched again.
