import { useState } from "react";

import {
  type Cell,
  type Graveyard,
  type JobKind,
  type Missing,
  type SearchDetail,
  type Strategy,
  getGraveyard,
} from "../api";
import { Chart } from "../components/Chart";
import { Term, Waiting } from "../components/Term";
import { type Searches, loadSearches, useLoad } from "../useLoad";
import { REASON_LABEL, gatePill, reasonDetail, signedPct, versionLabel, whenLabel } from "../words";

/** The colour scale the prototype uses for the grid: dark is a low score, bright a high one. */
const SCALE: [number, number, number][] = [
  [68, 1, 84],
  [72, 40, 120],
  [62, 74, 137],
  [49, 104, 142],
  [38, 130, 142],
  [31, 158, 137],
  [53, 183, 121],
  [109, 205, 89],
  [180, 222, 44],
  [253, 231, 37],
];

/** The colour for a score placed between the grid's lowest (0) and highest (1). */
export function ramp(position: number): string {
  const scaled = Math.max(0, Math.min(1, position)) * (SCALE.length - 1);
  const index = Math.min(SCALE.length - 2, Math.floor(scaled));
  const from = SCALE[index] ?? [0, 0, 0];
  const to = SCALE[index + 1] ?? from;
  return `rgb(${from.map((channel, k) => Math.round(channel + ((to[k] ?? channel) - channel) * (scaled - index))).join(",")})`;
}

type Tab = "grid" | "ruled" | "past";
const TABS: [Tab, string][] = [
  ["grid", "Settings"],
  ["ruled", "Ruled out"],
  ["past", "Past searches"],
];

function sameVersion(a: Record<string, number>, b: Record<string, number>): boolean {
  return Object.keys(a).length === Object.keys(b).length && Object.entries(a).every(([name, value]) => b[name] === value);
}

function Grid({ search }: { search: SearchDetail }) {
  const [across, down, ...rest] = search.parameters;
  const candidate = search.cells.find((cell) => cell.mark === "candidate") ?? search.cells[0];
  const [chosen, setChosen] = useState<Record<string, number> | null>(candidate?.params ?? null);
  const [slice, setSlice] = useState<Record<string, number>>(() =>
    Object.fromEntries(rest.map((name) => [name, candidate?.params[name] ?? search.axes[name]?.[0] ?? 0])),
  );

  if (across === undefined || candidate === undefined || chosen === null) {
    return (
      <div className="empty">
        <b>This search tried no versions</b>
        There is no grid to show.
      </div>
    );
  }

  const acrossValues = search.axes[across] ?? [];
  const downValues = down === undefined ? [null] : (search.axes[down] ?? []);
  const scores = search.cells.map((cell) => cell.sharpe).filter((score): score is number => score !== null);
  const low = Math.min(...scores);
  const high = Math.max(...scores);

  const find = (a: number, d: number | null): Cell | undefined =>
    search.cells.find(
      (cell) =>
        cell.params[across] === a &&
        (down === undefined || cell.params[down] === d) &&
        rest.every((name) => cell.params[name] === slice[name]),
    );
  const selected = search.cells.find((cell) => sameVersion(cell.params, chosen)) ?? candidate;
  const gate = gatePill(search.gate);

  return (
    <div className="grid2">
      <div className="card">
        <div className="between">
          <h2>{down === undefined ? `${across} (columns)` : `${down} (rows) by ${across} (columns)`}</h2>
          {rest.map((name) => (
            <span key={name}>
              {name}{" "}
              <span className="seg" role="group" aria-label={name}>
                {(search.axes[name] ?? []).map((value) => (
                  <button
                    type="button"
                    key={value}
                    aria-pressed={slice[name] === value}
                    onClick={() => setSlice({ ...slice, [name]: value })}
                  >
                    {value}
                  </button>
                ))}
              </span>
            </span>
          ))}
        </div>
        <div
          className="heat"
          style={{ gridTemplateColumns: `auto repeat(${acrossValues.length}, minmax(2.4rem, 1fr))` }}
        >
          <span className="lab" />
          {acrossValues.map((value) => (
            <span className="lab" key={value}>
              {value}
            </span>
          ))}
          {downValues.map((d) => [
            <span className="lab" key={`label-${d}`}>
              {d}
            </span>,
            ...acrossValues.map((a) => {
              const cell = find(a, d);
              if (cell === undefined) {
                return <span key={`${a}-${d}`} />;
              }
              const position = cell.sharpe === null || high === low ? 0 : (cell.sharpe - low) / (high - low);
              const classes = ["cell", cell === selected ? "sel" : "", cell.mark === "candidate" ? "best" : ""];
              return (
                <button
                  type="button"
                  key={`${a}-${d}`}
                  className={classes.filter(Boolean).join(" ")}
                  style={
                    cell.sharpe === null
                      ? { background: "var(--rule)", color: "var(--ink-soft)" }
                      : { background: ramp(position), color: position > 0.62 ? "#16181d" : "#fff" }
                  }
                  aria-label={`${versionLabel(cell.params)}, ${cell.sharpe === null ? "no score" : `score ${cell.sharpe.toFixed(2)}`}`}
                  aria-pressed={cell === selected}
                  onClick={() => setChosen(cell.params)}
                >
                  {cell.sharpe === null ? "–" : cell.sharpe.toFixed(2)}
                </button>
              );
            }),
          ])}
        </div>
        <p className="soft" style={{ fontSize: "var(--t-s)" }}>
          Each square is one version, scored on prices from {search.window.start.slice(0, 4)} to{" "}
          {search.window.end.slice(0, 4)}. Brighter is better. ★: the version the search put forward. The numbers on
          the edges are lengths in trading days. Click any square.
        </p>
      </div>

      <div className="card">
        <div className="between">
          <h2>{versionLabel(selected.params)}</h2>
          {selected.mark === "candidate" ? (
            <span className="pill warn">Best found</span>
          ) : selected.mark !== null ? (
            <span className="pill bad">{REASON_LABEL[selected.mark]}</span>
          ) : null}
        </div>
        {selected.curve === null ? (
          <p className="soft">The replay of this version stopped early, so it has no growth line and no score.</p>
        ) : (
          <Chart dates={search.dates} values={selected.curve} height={170} name="This strategy" />
        )}
        <div className="rows">
          <div className="rowi">
            <span>
              <Term word="sharpe" /> on old prices
            </span>
            <b className="mono">{selected.sharpe === null ? "none" : selected.sharpe.toFixed(2)}</b>
          </div>
          <div className="rowi">
            <span>Total return</span>
            <b className="mono">{selected.total_return_pct === null ? "none" : signedPct(selected.total_return_pct)}</b>
          </div>
          <div className="rowi">
            <span>
              <Term word="fall" />
            </span>
            <b className="mono">
              {selected.max_drawdown_pct === null ? "none" : `−${selected.max_drawdown_pct.toFixed(1)}%`}
            </b>
          </div>
          <div className="rowi">
            <span>Trades</span>
            <b className="mono">{selected.trade_count}</b>
          </div>
          <div className="rowi">
            <span>
              <Term word="luck" />
            </span>
            {selected.mark === "candidate" ? (
              <span className={`pill ${gate.tone}`}>{gate.text}</span>
            ) : (
              <span className="pill">Not run on this version</span>
            )}
          </div>
        </div>
        {selected.max_drawdown_pct !== null && selected.max_drawdown_pct > search.max_drawdown_ceiling_pct && (
          <p className="soft" style={{ fontSize: "var(--t-s)" }}>
            Its worst fall is above the {search.max_drawdown_ceiling_pct}% limit, so the search could not put it
            forward.
          </p>
        )}
        <button type="button" className="btn go" disabled>
          Deploy this version…
        </button>
        <p className="soft" style={{ fontSize: "var(--t-s)" }}>
          Deploying is not built yet, and unlocks only for a version that passes the luck test. The luck test, at
          the top of this page, is run on the version a search puts forward and not on a chosen square. A higher
          score on old prices alone is often just luck.
        </p>
      </div>
    </div>
  );
}

function RuledOut() {
  const load = useLoad<Graveyard | Missing>("graveyard", getGraveyard);
  if (load.state !== "ready") {
    return <p className="soft">{load.state === "failed" ? load.message : "Reading…"}</p>;
  }
  const graveyard = load.value;
  if (!graveyard.available) {
    return (
      <div className="empty" role="alert">
        <b>The ruled-out list could not be read</b>
        {graveyard.reason}
      </div>
    );
  }
  if (graveyard.entries.length === 0) {
    return (
      <div className="empty">
        <b>Nothing ruled out yet</b>
        Versions a search rejects are listed here after it runs.
      </div>
    );
  }
  return (
    <div className="card">
      <p className="soft">
        Versions already rejected, kept so the same dead end is not tried twice. Updated automatically after every
        search. This list covers the whole project: a rejection is not recorded against one strategy.{" "}
        {graveyard.records} rejections are on record, {graveyard.entries.length} of them different.
        {graveyard.unread_lines > 0 && ` ${graveyard.unread_lines} more lines in the record could not be read.`}
      </p>
      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>Version</th>
              <th>Reason</th>
              <th>Detail</th>
              <th className="n">Times</th>
              <th style={{ paddingLeft: "1rem" }}>Last recorded</th>
            </tr>
          </thead>
          <tbody>
            {graveyard.entries.map((entry) => (
              <tr key={`${versionLabel(entry.candidate)}-${entry.reason}`}>
                <td className="mono">{versionLabel(entry.candidate)}</td>
                <td>
                  <span className="pill bad">{REASON_LABEL[entry.reason]}</span>
                </td>
                <td className="wrap soft">{reasonDetail(entry)}</td>
                <td className="n">{entry.times}</td>
                <td style={{ paddingLeft: "1rem" }}>{whenLabel(entry.last_recorded_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Past({ searches }: { searches: Searches }) {
  return (
    <div className="card">
      <p className="soft">Every search on record for this strategy, newest first.</p>
      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>Best version found</th>
              <th className="n">Score</th>
              <th style={{ paddingLeft: "1rem" }}>Luck test</th>
            </tr>
          </thead>
          <tbody>
            {searches.list.map((entry) =>
              entry.readable ? (
                <tr key={entry.artifact}>
                  <td>{whenLabel(entry.generated_at)}</td>
                  <td className="mono">{versionLabel(entry.candidate)}</td>
                  <td className="n">{entry.sharpe === null ? "none" : entry.sharpe.toFixed(2)}</td>
                  <td style={{ paddingLeft: "1rem" }}>
                    <span className={`pill ${gatePill(entry.gate).tone}`}>{gatePill(entry.gate).text}</span>
                    {entry.config_changed_since && (
                      <span className="soft"> strategy file changed since</span>
                    )}
                  </td>
                </tr>
              ) : (
                <tr key={entry.artifact}>
                  <td>{whenLabel(entry.generated_at)}</td>
                  <td className="wrap soft" colSpan={3}>
                    Not read: {entry.reason}.
                  </td>
                </tr>
              ),
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function Improve({ strategy, onStart }: { strategy: Strategy; onStart: (kind: JobKind) => void }) {
  const [tab, setTab] = useState<Tab>("grid");
  const load = useLoad<Searches>(strategy.id, () => loadSearches(strategy.id));
  if (load.state !== "ready") {
    return <Waiting title="Improve" message={load.state === "failed" ? load.message : null} />;
  }
  const searches = load.value;
  const { newest } = searches;

  return (
    <>
      <div className="head">
        <div>
          <h1>Improve</h1>
          <p>
            Other settings for {strategy.name}, from the last search
            {newest?.available ? ` (${whenLabel(newest.generated_at)})` : ""}.
          </p>
        </div>
        <span className="between">
          <button type="button" className="btn" onClick={() => onStart("luck_test")}>
            Run the luck test
          </button>
          <button type="button" className="btn primary" onClick={() => onStart("search")}>
            Search again
          </button>
        </span>
      </div>
      <div className="tabs" role="tablist">
        {TABS.map(([id, label]) => (
          <button type="button" role="tab" key={id} aria-selected={tab === id} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </div>
      {tab === "ruled" ? (
        <RuledOut />
      ) : tab === "past" ? (
        searches.list.length === 0 ? (
          <div className="empty">
            <b>No searches yet</b>
            None has been run for this strategy.
          </div>
        ) : (
          <Past searches={searches} />
        )
      ) : newest === null ? (
        <div className="empty">
          <b>No search to show</b>
          {searches.list.length === 0
            ? "No search has been run for this strategy. Today one is started from the command line."
            : "Searches are on record, but none could be read. See Past searches."}
        </div>
      ) : !newest.available ? (
        <div className="empty" role="alert">
          <b>The last search could not be read</b>
          {newest.reason}
        </div>
      ) : (
        <Grid search={newest} key={newest.artifact} />
      )}
    </>
  );
}
