import { useState } from "react";

import type { SearchDetail, Strategy } from "../api";
import { Chart } from "../components/Chart";
import { Term } from "../components/Term";
import { type Searches, loadSearches, useLoad } from "../useLoad";
import { gatePill, signedPct, versionLabel, whenLabel } from "../words";

/** The figures a strategy's newest search recorded for the version it put forward, or null when there are none. */
export function bestOf(search: SearchDetail | null) {
  if (search === null) {
    return null;
  }
  return search.cells.find((cell) => cell.mark === "candidate") ?? null;
}

/** Which side a row marks as better: "a", "b", or neither when either side has no figure or they tie. */
export function better(a: number | null | undefined, b: number | null | undefined, higher: boolean): "a" | "b" | null {
  if (a == null || b == null || a === b) {
    return null;
  }
  return (a > b) === higher ? "a" : "b";
}

function Side({ strategy }: { strategy: Strategy }) {
  const load = useLoad<Searches>(strategy.id, () => loadSearches(strategy.id));
  if (load.state !== "ready") {
    return <p className="soft">{load.state === "failed" ? load.message : "Reading…"}</p>;
  }
  const search = load.value.newest !== null && load.value.newest.available ? load.value.newest : null;
  const best = bestOf(search);
  if (search === null || best === null || best.curve === null) {
    return <p className="soft">{strategy.name} has no searched version to show.</p>;
  }
  return <Chart dates={search.dates} values={best.curve} height={200} name={strategy.name} />;
}

/** Two strategies side by side, on what their newest searches recorded. */
export function Compare({ strategies }: { strategies: Strategy[] }) {
  // Opens on the first two strategies with a search to compare, before any without.
  const ordered = [
    ...strategies.filter((strategy) => strategy.latest_search !== null),
    ...strategies.filter((strategy) => strategy.latest_search === null),
  ];
  const [a, setA] = useState(ordered[0]?.id ?? "");
  const [b, setB] = useState(ordered[1]?.id ?? "");
  const left = strategies.find((strategy) => strategy.id === a);
  const right = strategies.find((strategy) => strategy.id === b);
  const loadA = useLoad<Searches | null>(`compare-${a}`, () => (left ? loadSearches(left.id) : Promise.resolve(null)));
  const loadB = useLoad<Searches | null>(`compare-${b}`, () => (right ? loadSearches(right.id) : Promise.resolve(null)));

  if (strategies.length < 2) {
    return (
      <>
        <div className="head">
          <div>
            <h1>Compare</h1>
          </div>
        </div>
        <div className="empty">
          <b>One strategy so far</b>
          Compare puts two strategies side by side. Add another to configs/ - by hand, or read from a video with
          /watch in Claude Code - and it appears here.
        </div>
      </>
    );
  }

  const detail = (load: typeof loadA): SearchDetail | null =>
    load.state === "ready" && load.value !== null && load.value.newest !== null && load.value.newest.available
      ? load.value.newest
      : null;
  const searchA = detail(loadA);
  const searchB = detail(loadB);
  const bestA = bestOf(searchA);
  const bestB = bestOf(searchB);

  function select(side: "a" | "b") {
    return (
      <select
        aria-label={side === "a" ? "Strategy A" : "Strategy B"}
        value={side === "a" ? a : b}
        onChange={(event) => (side === "a" ? setA : setB)(event.target.value)}
      >
        {strategies.map((strategy) => (
          <option key={strategy.id} value={strategy.id}>
            {strategy.name}
          </option>
        ))}
      </select>
    );
  }

  const rows: [string, number | null | undefined, number | null | undefined, (value: number) => string, boolean][] = [
    ["Score (Sharpe) on old prices", bestA?.sharpe, bestB?.sharpe, (value) => value.toFixed(2), true],
    ["Total return", bestA?.total_return_pct, bestB?.total_return_pct, signedPct, true],
    ["Worst fall", bestA?.max_drawdown_pct, bestB?.max_drawdown_pct, (value) => `−${value.toFixed(1)}%`, false],
    ["Trades", bestA?.trade_count, bestB?.trade_count, (value) => String(value), true],
  ];

  return (
    <>
      <div className="head">
        <div>
          <h1>Compare</h1>
          <p>
            Two strategies side by side, on what their newest searches recorded on old prices. ◀ marks the better
            one. A higher score on old prices alone is often luck: the luck test row says which passed.
          </p>
        </div>
        <span className="between">
          {select("a")}
          <span className="soft">against</span>
          {select("b")}
        </span>
      </div>
      <div className="grid2">
        <div className="card">
          <h2>Growth of each best version</h2>
          {left !== undefined && <Side strategy={left} key={`a-${left.id}`} />}
          {right !== undefined && <Side strategy={right} key={`b-${right.id}`} />}
        </div>
        <div className="card">
          <div className="tablewrap side">
            <table>
              <thead>
                <tr>
                  <th />
                  <th className="n">{left?.name}</th>
                  <th className="n">{right?.name}</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Best version</td>
                  <td className="n mono">{versionLabel(searchA?.candidate ?? null)}</td>
                  <td className="n mono">{versionLabel(searchB?.candidate ?? null)}</td>
                </tr>
                {rows.map(([name, x, y, render, higher]) => {
                  const mark = better(x, y, higher);
                  return (
                    <tr key={name}>
                      <td>{name === "Worst fall" ? <Term word="fall" /> : name}</td>
                      <td className="n">
                        {x == null ? "none" : mark === "a" ? <b>{render(x)} ◀</b> : render(x)}
                      </td>
                      <td className="n">
                        {y == null ? "none" : mark === "b" ? <b>{render(y)} ◀</b> : render(y)}
                      </td>
                    </tr>
                  );
                })}
                <tr>
                  <td>
                    <Term word="luck" />
                  </td>
                  <td className="n">{searchA === null ? "none" : gatePill(searchA.gate).text}</td>
                  <td className="n">{searchB === null ? "none" : gatePill(searchB.gate).text}</td>
                </tr>
                <tr>
                  <td>Searched</td>
                  <td className="n">{searchA === null ? "never" : whenLabel(searchA.generated_at)}</td>
                  <td className="n">{searchB === null ? "never" : whenLabel(searchB.generated_at)}</td>
                </tr>
              </tbody>
            </table>
          </div>
          {a === b && <p className="soft">Both sides are the same strategy.</p>}
        </div>
      </div>
    </>
  );
}
