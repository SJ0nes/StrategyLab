import { SECTIONS, type SectionId } from "../components/Nav";

/** What each unbuilt section will hold, so its empty page says more than "soon". */
const WILL_HOLD: Record<Exclude<SectionId, "dashboard">, string> = {
  trades: "Every order a deployed strategy has placed, and what it decided on each day.",
  tests: "Every check run on a strategy: backtests, luck tests, the final exam, live against backtest.",
  backtest: "A replay of the strategy on old prices, with its growth chart and figures.",
  improve: "The grid of other settings, the versions already ruled out, and past searches.",
  compare: "Two strategies side by side.",
};

export function NotBuilt({ section }: { section: Exclude<SectionId, "dashboard"> }) {
  const label = SECTIONS.find((item) => item.id === section)?.label ?? section;
  return (
    <>
      <div className="head">
        <div>
          <h1>{label}</h1>
        </div>
      </div>
      <div className="empty">
        <b>Not built yet</b>
        {WILL_HOLD[section]} The same work can be done today from the command line.
      </div>
    </>
  );
}
