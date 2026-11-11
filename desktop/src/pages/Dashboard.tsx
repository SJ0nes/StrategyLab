import type { LatestSearch, Overview, Strategy } from "../api";

/** The luck test's outcome as one sentence and a colour. */
export function gateLine(search: LatestSearch | null): { tone: "good" | "bad" | "warn" | ""; text: string } {
  if (search === null) {
    return { tone: "", text: "No search has been run for this strategy." };
  }
  const { gate } = search;
  switch (gate.status) {
    case "passed":
      return { tone: "good", text: `Passed the luck test: beat ${gate.beaten} of ${gate.scoreable_trials}.` };
    case "failed":
      return {
        tone: "bad",
        text: `Failed the luck test: beat ${gate.beaten} of ${gate.scoreable_trials}, needed ${gate.required}.`,
      };
    case "undetermined":
      return { tone: "warn", text: `Luck test not decided: only ${gate.scoreable_trials} trials were run.` };
    case "not_computed":
      return { tone: "warn", text: "Searched without a luck test." };
    case "no_candidate":
      return { tone: "warn", text: "The search found no version to put forward." };
  }
}

function settings(candidate: Record<string, number> | null): string {
  if (candidate === null) {
    return "none";
  }
  return Object.entries(candidate)
    .map(([name, value]) => `${name} ${value}`)
    .join(", ");
}

export function Dashboard({ overview, strategy }: { overview: Overview; strategy: Strategy }) {
  const search = strategy.latest_search;
  const line = gateLine(search);
  return (
    <>
      <div className="head">
        <div>
          <h1>
            {strategy.name}{" "}
            <span className="pill warn" style={{ verticalAlign: "middle" }}>
              Draft
            </span>
          </h1>
          <p>{strategy.description}</p>
        </div>
      </div>

      <div className={`rule`} style={{ borderLeft: `.3rem solid var(--${line.tone === "" ? "rule" : line.tone === "warn" ? "warn-rule" : line.tone})` }}>
        <b>{line.text}</b>
        <br />
        <span className="soft">
          Not deployed. Deploying is not built yet, and nothing may be deployed before it passes the luck test.
        </span>
      </div>

      <div className="kpis">
        <div className="card kpi">
          <span className="label">Best version found</span>
          <span className="big" style={{ fontSize: "var(--t-l)" }}>{settings(search?.candidate ?? null)}</span>
          <small>
            {search === null
              ? "no search yet"
              : search.config_changed_since
                ? `from ${search.cells} versions tried, before the strategy file was last changed`
                : `from ${search.cells} versions tried`}
          </small>
        </div>
        <div className="card kpi">
          <span className="label">Its score (Sharpe) on old prices</span>
          <span className="big">{search?.sharpe == null ? "none" : search.sharpe.toFixed(2)}</span>
          <small>{search?.max_drawdown_pct == null ? "" : `worst fall −${search.max_drawdown_pct.toFixed(1)}%`}</small>
        </div>
        <div className="card kpi">
          <span className="label">Looks at unseen years used</span>
          <span className="big">{overview.held_back_reads}</span>
          <small>each look is permanent</small>
        </div>
      </div>

      <div className="empty">
        <b>No live figures</b>
        Return, win rate, account value and trades appear here once a strategy is deployed. That part of the app is
        not built yet.
      </div>
    </>
  );
}
