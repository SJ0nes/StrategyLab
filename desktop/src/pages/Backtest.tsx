import type { Strategy } from "../api";
import { Chart } from "../components/Chart";
import { Term, Waiting } from "../components/Term";
import { type Searches, loadSearches, useLoad } from "../useLoad";
import { dayLabel, money, signedPct, versionLabel, whenLabel } from "../words";

/**
 * The replay the last search recorded for the version it put forward.
 *
 * Nothing is run here and no figure is worked out here. The window can show one
 * period only, the one the search covered: the recorded growth line is thinned
 * for drawing, and a score recomputed from a slice of it would be a second
 * formula on rounded data that disagrees with the recorded one.
 */
export function Backtest({ strategy }: { strategy: Strategy }) {
  const load = useLoad<Searches>(strategy.id, () => loadSearches(strategy.id));
  if (load.state !== "ready") {
    return <Waiting title="Backtest" message={load.state === "failed" ? load.message : null} />;
  }
  const { newest } = load.value;
  const best = newest?.available ? newest.cells.find((cell) => cell.mark === "candidate") : undefined;

  return (
    <>
      <div className="head">
        <div>
          <h1>Backtest</h1>
          <p>A replay of {strategy.name} on old prices, to see what it would have done. Costs of trading are included.</p>
        </div>
        <button
          type="button"
          className="btn primary"
          disabled
          title="Running a new backtest from the app is not built yet"
        >
          Run backtest
        </button>
      </div>

      {newest === null ? (
        <div className="empty">
          <b>No replay on record</b>
          No search that could be read has been run for this strategy, and a search is what records a replay today.
        </div>
      ) : !newest.available ? (
        <div className="empty" role="alert">
          <b>The last search could not be read</b>
          {newest.reason}
        </div>
      ) : best === undefined ? (
        <div className="empty">
          <b>The last search put no version forward</b>
          So there is no single replay to show. Improve shows every version it tried.
        </div>
      ) : (
        <>
          <div className="rule">
            <b>{versionLabel(best.params)}</b>, the best of {newest.cells.length} versions tried by the search of{" "}
            {whenLabel(newest.generated_at)}.{" "}
            <span className="soft">
              The best of many tries looks better than it is; the luck test on the Tests page says by how much.
              Running a new backtest, or choosing another period, is not built yet.
            </span>
          </div>
          <div className="kpis">
            <div className="card kpi">
              <span className="label">Total return</span>
              <span className={`big ${best.total_return_pct === null ? "" : best.total_return_pct < 0 ? "down" : "up"}`}>
                {best.total_return_pct === null ? "none" : signedPct(best.total_return_pct)}
              </span>
            </div>
            <div className="card kpi">
              <span className="label">
                <Term word="sharpe" />
              </span>
              <span className="big">{best.sharpe === null ? "none" : best.sharpe.toFixed(2)}</span>
            </div>
            <div className="card kpi">
              <span className="label">
                <Term word="fall" />
              </span>
              <span className="big">
                {best.max_drawdown_pct === null ? "none" : `−${best.max_drawdown_pct.toFixed(1)}%`}
              </span>
            </div>
            <div className="card kpi">
              <span className="label">Trades</span>
              <span className="big">{best.trade_count}</span>
            </div>
          </div>
          <div className="card">
            <h2>
              Growth of {money(newest.starting_equity)} on {newest.instrument}, {dayLabel(newest.window.start)} to{" "}
              {dayLabel(newest.window.end)}
            </h2>
            {best.curve === null ? (
              <p className="soft">No growth line was recorded for this version.</p>
            ) : (
              <Chart dates={newest.dates} values={best.curve} height={280} name="This strategy" />
            )}
            <p className="soft" style={{ fontSize: "var(--t-s)" }}>
              A backtest is a replay, not a promise.
            </p>
          </div>
        </>
      )}
    </>
  );
}
