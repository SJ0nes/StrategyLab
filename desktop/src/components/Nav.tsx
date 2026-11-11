/** The six sections, in the two groups the approved prototype shows. */
export const SECTIONS = [
  { id: "dashboard", label: "Dashboard", group: "Live" },
  { id: "trades", label: "Trades", group: "Live" },
  { id: "tests", label: "Tests", group: "Live" },
  { id: "backtest", label: "Backtest", group: "Workshop" },
  { id: "improve", label: "Improve", group: "Workshop" },
  { id: "compare", label: "Compare", group: "Workshop" },
] as const;

export type SectionId = (typeof SECTIONS)[number]["id"];

const GROUPS = ["Live", "Workshop"] as const;

export function Nav({ current }: { current: SectionId }) {
  return (
    <nav aria-label="Sections">
      {GROUPS.map((group) => (
        <div key={group} data-tour={group === "Live" ? "live" : "lab"}>
          <div className="grp">{group}</div>
          {SECTIONS.filter((section) => section.group === group).map((section) => (
            <a
              key={section.id}
              href={`#${section.id}`}
              aria-current={section.id === current ? "page" : undefined}
              style={{ display: "block" }}
            >
              {section.label}
            </a>
          ))}
        </div>
      ))}
    </nav>
  );
}
