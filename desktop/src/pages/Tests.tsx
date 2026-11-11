import { Fragment, type ReactNode, useState } from "react";

import {
  type Exam,
  type Overview,
  type SearchEntry,
  type Strategy,
  getExamStatus,
  getExams,
  getSearches,
} from "../api";
import { ExamDialog } from "../components/ExamDialog";
import { Term, Waiting } from "../components/Term";
import { useLoad } from "../useLoad";
import { gatePill, gateSentence, versionLabel, whenLabel } from "../words";

interface Row {
  key: string;
  /** When the test was generated, for ordering. Empty when the file does not say. */
  at: string;
  test: ReactNode;
  what: string;
  tone: "good" | "bad" | "warn" | "";
  result: string;
  detail: string;
}

const EXAM_RESULT: Record<"pass" | "fail" | "insufficient_data", [Row["tone"], string]> = {
  pass: ["good", "Passed"],
  fail: ["bad", "Failed"],
  insufficient_data: ["warn", "Too few trades to judge"],
};

/** One row per search on record: a search is a grid of backtests, and a luck test when it ran one. */
export function searchRow(entry: SearchEntry): Row {
  if (!entry.readable) {
    return {
      key: entry.artifact,
      at: entry.generated_at ?? "",
      test: "Search",
      what: "Not read.",
      tone: "",
      result: "Not read",
      detail: `This search was not read: ${entry.reason}.`,
    };
  }
  const pill = gatePill(entry.gate);
  const ranLuckTest = ["passed", "failed", "undetermined"].includes(entry.gate.status);
  return {
    key: entry.artifact,
    at: entry.generated_at ?? "",
    test: ranLuckTest ? <Term word="luck" /> : "Search",
    what: ranLuckTest
      ? `Tried ${entry.cells} versions on old prices, then compared the best against ${entry.gate.scoreable_trials} scrambled histories.`
      : `Tried ${entry.cells} versions on old prices.`,
    tone: pill.tone,
    result: pill.text,
    detail: `Best version: ${versionLabel(entry.candidate)}${entry.sharpe === null ? "" : `, score ${entry.sharpe.toFixed(2)}`}. ${gateSentence(entry.gate)}${entry.config_changed_since ? " The strategy file has changed since this ran." : ""}`,
  };
}

/** One row per read of the unseen years on record. */
export function examRow(exam: Exam): Row {
  if (!exam.readable) {
    return {
      key: exam.artifact,
      at: exam.generated_at ?? "",
      test: <Term word="exam" />,
      what: "Not read.",
      tone: "",
      result: "Not read",
      detail: `This result was not read: ${exam.reason}.`,
    };
  }
  const [tone, result] = EXAM_RESULT[exam.outcome] ?? ["", exam.outcome];
  const figures = [
    exam.sharpe === null ? null : `score ${exam.sharpe.toFixed(2)}`,
    exam.max_drawdown_pct === null ? null : `worst fall −${exam.max_drawdown_pct.toFixed(1)}%`,
    exam.trades === null ? null : `${exam.trades} trades`,
    exam.folds === null ? null : `${exam.folds} periods`,
  ].filter((figure) => figure !== null);
  return {
    key: exam.artifact,
    at: exam.generated_at ?? "",
    test: <Term word="exam" />,
    what: `One look at unseen years${exam.read_number === null ? "" : ` (look number ${exam.read_number})`}.`,
    tone,
    result,
    detail: `Version tested: ${versionLabel(exam.params)}. On the unseen years: ${figures.join(", ")}. ${exam.reason}`,
  };
}

/** Why the final exam cannot be taken from this window, in one sentence. */
export function examNote(strategy: Strategy, refusal: string | null = null): string {
  if (refusal === null && strategy.latest_search?.gate.status === "passed") {
    return "This strategy passed the luck test and may take the final exam: one look at the unseen years.";
  }
  if (refusal !== null && strategy.latest_search?.gate.status === "passed") {
    return `Final exam is locked: ${refusal}.`;
  }
  return "Final exam is locked: this strategy has not passed the luck test.";
}

export function Tests({
  overview,
  strategy,
  onExamStarted,
}: {
  overview: Overview;
  strategy: Strategy;
  onExamStarted?: () => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [examining, setExamining] = useState(false);
  const exam = useLoad(`${strategy.id}-exam`, () => getExamStatus(strategy.id));
  const refusal = exam.state === "ready" ? exam.value.refusal : "its exam status could not be read";
  const load = useLoad(strategy.id, async () => ({
    searches: await getSearches(strategy.id),
    exams: await getExams(strategy.id),
  }));
  if (load.state !== "ready") {
    return <Waiting title="Tests" message={load.state === "failed" ? load.message : null} />;
  }
  const rows = [...load.value.searches.map(searchRow), ...load.value.exams.map(examRow)].sort((a, b) =>
    b.at.localeCompare(a.at),
  );

  return (
    <>
      <div className="head">
        <div>
          <h1>Tests</h1>
          <p>Every check on record for {strategy.name}, newest first.</p>
        </div>
        <span className="between">
          <button type="button" className="btn" disabled title="Not in the app yet: strategylab diff checks live decisions against the backtest">
            Check live vs backtest
          </button>
          <button
            type="button"
            className="btn go"
            disabled={refusal !== null}
            title={examNote(strategy, refusal)}
            onClick={() => setExamining(true)}
          >
            Take final exam…
          </button>
        </span>
      </div>

      <div className="kpis">
        <div className="card kpi">
          <span className="label">Looks at unseen years used</span>
          <span className="big">{overview.held_back_reads}</span>
          <small>each look is permanent</small>
        </div>
        <div className="card kpi">
          <span className="label">Live days matching backtest</span>
          <span className="big">none</span>
          <small>not in the app yet; strategylab diff checks them</small>
        </div>
        <div className="card kpi">
          <span className="label">Tests on record</span>
          <span className="big">{rows.length}</span>
          <small>click a row for detail</small>
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="empty">
          <b>No tests yet</b>
          Nothing has been run for this strategy.
        </div>
      ) : (
        <div className="card">
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Test</th>
                  <th>Date</th>
                  <th>What it did</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <Fragment key={row.key}>
                    <tr
                      className="click"
                      tabIndex={0}
                      aria-expanded={open === row.key}
                      onClick={() => setOpen(open === row.key ? null : row.key)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          setOpen(open === row.key ? null : row.key);
                        }
                      }}
                    >
                      <td>
                        <b>{row.test}</b>
                      </td>
                      <td>{row.at === "" ? "date not recorded" : whenLabel(row.at)}</td>
                      <td className="soft wrap">{row.what}</td>
                      <td>
                        <span className={`pill ${row.tone}`}>{row.result}</span>
                      </td>
                    </tr>
                    {open === row.key && (
                      <tr>
                        <td className="wrap soft" colSpan={4}>
                          {row.detail}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
          <p className="soft" style={{ fontSize: "var(--t-s)" }}>
            {examNote(strategy, refusal)} A single backtest is not kept on record; each search above is a grid of
            them.
          </p>
        </div>
      )}
      {examining && (
        <ExamDialog
          strategy={strategy}
          looks={overview.held_back_reads}
          onClose={() => setExamining(false)}
          onStarted={() => {
            setExamining(false);
            onExamStarted?.();
          }}
        />
      )}
    </>
  );
}
