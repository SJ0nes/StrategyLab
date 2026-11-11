import { SECTIONS, type SectionId } from "../components/Nav";

/** What each unbuilt section will hold, so its empty page says more than "soon". */
type Unbuilt = Extract<SectionId, "trades" | "compare">;

const WILL_HOLD: Record<Unbuilt, string> = {
  trades: "Every order a deployed strategy has placed, and what it decided on each day.",
  compare: "Two strategies side by side.",
};

export function NotBuilt({ section }: { section: Unbuilt }) {
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
