---
name: report-design
description: Use when building, changing, or reviewing any HTML report this project produces - heatmaps, equity curves, walk-forward fold displays, noise-ceiling comparisons, run-to-run diffs. Covers the interactions the reports must support, the self-contained inline-SVG technique, and the styling rules that keep every report reading as one product.
---

# Report design

These reports are **tools the operator returns to**, not documents produced once and filed. That
is the whole design brief. Most of what follows is about interaction, because that is what
separates a tool from a picture.

## Interaction

**Cell to curve in one interaction.** Hovering a grid cell reveals its metrics - Sharpe, trade
count, drawdown - with no page load. Clicking it shows that cell's equity curve. "What does this
cell actually look like" is the question asked most often, and it must never cost a round trip.

**The noise ceiling is drawn on the same axis as the result.** Never in a table underneath it.
If a winning cell sits inside the noise distribution, that has to be *visible* - the eye should
land on it before the mind computes anything. A number in a footnote saying the 95th percentile
of shuffled returns was 1.4 is a fact the reader can skip. A shaded band the winner sits inside
is one they cannot.

**Fold results sit side by side.** An inconsistent walk-forward should read as a shape - four
folds where one is tall and three are flat is recognisable at a glance and unmistakable. Stacked
or paginated folds hide exactly the inconsistency the walk-forward exists to expose.

**Two result sets load and compare.** This week's search diffed against last week's. Research is
iterative; a report that cannot answer "what changed" forces the operator into a spreadsheet,
and at that point the tool has failed.

**`insufficient_data` is displayed as prominently as a pass or a fail.** Its own visual
treatment, the same weight as any other outcome, never a blank cell, never a grey tile that
reads as "nothing here". Burying an honest non-result is the one thing that makes a research
tool untrustworthy - and once the operator suspects the tool is flattering them, every number in
it is worthless.

**Every number is traceable.** Protocol version, fixture hash, and the exact command that
produced the result are available on the figure itself - not in a sibling file, not in a log.
A figure that cannot say where it came from cannot be used to make a decision, and cannot be put
in front of anyone else.

## Technique

**Self-contained HTML. Hand-built inline SVG.** No CDN, no bundler, no build step, no external
requests of any kind. One file that opens instantly from disk, works offline, works in five
years, and can be emailed.

Charts are SVG elements written directly into the document. Interaction is a small amount of
plain JavaScript inline in the same file - hover handlers, a click handler, a comparison
toggle. Data is embedded as a JSON literal in a `<script>` tag.

Files stay small and open instantly, and **speed is most of what makes a tool feel good**. A
report that takes two seconds to appear gets opened half as often as one that appears at once,
and a report that is not opened does not inform anything.

## Styling

Restrained, and consistent across every report, so they read as one product rather than a
folder of one-off figures.

**Heatmaps use a perceptually uniform sequential ramp** - viridis, magma, or an equivalent.
**Never a rainbow ramp.** Rainbow ramps invent boundaries that are not in the data: the eye sees
a hard edge at the cyan-to-green transition where the underlying values change smoothly. This
project's entire argument is about telling a broad plateau from an isolated spike, and a ramp
that manufactures edges destroys exactly that distinction.

**One accent colour beyond the heatmap ramp.** It marks the selected cell, the noise ceiling,
the thing being compared against - one job at a time. A second accent starts a legend, and a
legend is a sign the figure has stopped being readable.

**Tabular numerals everywhere a figure appears.** Columns of numbers must align on the decimal
point. `font-variant-numeric: tabular-nums`, or a font stack that provides it.

**One font stack, one spacing scale.** Define both once, as CSS custom properties at the top of
the document, and use them in every report. Consistency here is what makes a set of reports feel
like an instrument rather than a pile of output.
