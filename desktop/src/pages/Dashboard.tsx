import { useState } from "react";

import { ApiError, type JobKind, type LatestSearch, type Strategy, confirmStrategy } from "../api";
import { Term } from "../components/Term";
import { gatePill, versionLabel, whenLabel } from "../words";

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

/** Where a step on the path to deployment stands. */
export type StepState = "done" | "here" | "failed" | "waiting";

export interface Step {
  title: string;
  state: StepState;
  note: string;
  /** The job a step's button starts, the confirmation it records, or nothing. */
  action: JobKind | "confirm" | null;
}

/**
 * The path to deployment for a strategy, worked out from what the server reports.
 *
 * A search counts only if it ran on the strategy file as it is now: one from
 * before an edit says nothing about the rules on screen. A failed luck test
 * offers no second try: the test is seeded, so the same file gives the same
 * answer, and trying seeds until one passes is the luck the test measures.
 */
export function draftSteps(strategy: Strategy): Step[] {
  const search = strategy.latest_search;
  const current = search !== null && !search.config_changed_since ? search : null;
  const confirmed = strategy.confirmed;
  const backtested = current !== null;
  const status = current?.gate.status;
  const passed = status === "passed";
  const failed = status === "failed";

  const written: Step = {
    title: strategy.source?.available ? "Read from a video" : "Written as a strategy file",
    state: "done",
    note:
      strategy.source?.available === true
        ? `By Claude Code with /watch${strategy.source.watched_at === null ? "" : `, ${strategy.source.watched_at}`}.`
        : `${strategy.config}.`,
    action: null,
  };
  const confirm: Step = {
    title: "You confirm the rules",
    state: confirmed ? "done" : "here",
    note: confirmed
      ? `Confirmed ${whenLabel(strategy.confirmed_at)}.`
      : strategy.stale
        ? "The file has changed since you last confirmed it. Read the rules again."
        : "Check them against where they came from. The reading can be wrong.",
    action: confirmed ? null : "confirm",
  };
  const backtest: Step = {
    title: "Backtest",
    state: backtested ? "done" : confirmed ? "here" : "waiting",
    note:
      current !== null
        ? `Best of ${current.cells ?? "?"} versions: ${versionLabel(current.candidate)}, score ${current.sharpe === null ? "none" : current.sharpe.toFixed(2)}.`
        : search !== null
          ? "The last one ran on an earlier version of the file. Replay every version on old prices. A few minutes."
          : "Replay every version on old prices. A few minutes.",
    action: confirmed && !backtested ? "search" : null,
  };
  const luck: Step = {
    title: "Luck test",
    state: passed ? "done" : failed ? "failed" : confirmed && backtested ? "here" : "waiting",
    note:
      current !== null && (passed || failed)
        ? `${gatePill(current.gate).text}.${failed ? " Its score is what a search this size finds on prices with no pattern in them. Running it again gives the same answer: change the rules instead." : ""}`
        : "Must beat 95 of 100 scrambled price histories. About an hour.",
    action: confirmed && backtested && !passed && !failed ? "luck_test" : null,
  };
  const deploy: Step = {
    title: "Deploy to practice account",
    state: "waiting",
    note: passed ? "Deploying is not built yet." : "Unlocks only after the luck test passes.",
    action: null,
  };
  return [written, confirm, backtest, luck, deploy];
}

const BUTTON: Record<NonNullable<Step["action"]>, string> = {
  confirm: "Rules are right",
  search: "Run backtest",
  luck_test: "Run luck test",
};

interface Props {
  strategy: Strategy;
  onStart: (kind: JobKind) => void;
  /** Read the project again: something on this page has changed it. */
  onChanged: () => void;
}

export function Dashboard({ strategy, onStart, onChanged }: Props) {
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { rules, source } = strategy;
  const fromVideo = source?.available === true;
  const steps = draftSteps(strategy);

  function press(action: NonNullable<Step["action"]>) {
    if (action !== "confirm") {
      onStart(action);
      return;
    }
    setBusy(true);
    setProblem(null);
    confirmStrategy(strategy.id, strategy.config_hash)
      .then(() => onChanged())
      .catch((error: unknown) => {
        setProblem(error instanceof ApiError ? error.message : "The confirmation could not be recorded.");
        // A refusal most often means the file changed: show the rules it holds now.
        onChanged();
      })
      .finally(() => setBusy(false));
  }

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
          <p>
            {strategy.description} Not deployed. It trades nothing until it passes the steps below.
          </p>
        </div>
      </div>

      <div className="grid2">
        <div className="card">
          <h2>{fromVideo ? "What the video says, as rules" : "Its rules"}</h2>
          <div className="rule">
            <span className="label">Buy when all of these are true</span>
            {rules.entry.map((line) => (
              <div key={line}>{line}</div>
            ))}
          </div>
          <div className="rule">
            <span className="label">Sell when{rules.exit.length > 1 ? " all of these are true" : ""}</span>
            {rules.exit.map((line) => (
              <div key={line}>{line}</div>
            ))}
          </div>
          <div className="rule">
            <span className="label">Settings to try: {rules.versions} versions</span>
            {[...rules.search, ...rules.fixed].map((line) => (
              <div key={line}>{line}</div>
            ))}
          </div>
          {source?.available === true && source.left_out.length > 0 && (
            <div className="rule" style={{ borderColor: "var(--warn-rule)", background: "var(--warn-bg)" }}>
              <span className="label" style={{ color: "var(--warn)" }}>
                From the video, but left out
              </span>
              {source.left_out.map((item) => (
                <div key={item.what}>
                  <b>{item.what}.</b> {item.why ?? ""}
                  {item.at === null ? "" : ` (at ${item.at})`}
                </div>
              ))}
            </div>
          )}
          {source?.available === true && source.rules.length > 0 && (
            <details>
              <summary className="soft">The rules as the video states them</summary>
              {source.rules.map((item) => (
                <div className="soft" key={item.rule}>
                  {item.at === null ? "" : `${item.at}: `}
                  {item.rule}
                </div>
              ))}
            </details>
          )}
          {source !== null && !source.available && (
            <div className="empty" role="alert">
              <b>Where it came from is not known</b>
              {source.reason}
            </div>
          )}
          <p className="soft" style={{ fontSize: "var(--t-s)" }}>
            File: <span className="mono">{strategy.config}</span>
            {source?.available === true && (
              <>
                {" "}
                · From {source.title === null ? "a video" : `"${source.title}"`}
                {source.channel === null ? "" : ` by ${source.channel}`}
                {source.url === null ? "" : <span className="mono"> {source.url}</span>}
              </>
            )}
          </p>
        </div>

        <div className="card">
          <h2>Path to deployment</h2>
          <div className="rows">
            {steps.map((step, index) => (
              <div className="rowi" key={step.title}>
                <span>
                  <span
                    className={`stepn ${step.state === "done" ? "done" : step.state === "here" ? "here" : ""}`}
                    aria-label={step.state}
                  >
                    {step.state === "done" ? "✓" : step.state === "failed" ? "✗" : index + 1}
                  </span>
                  <b>{step.title}</b>
                  <small style={{ marginLeft: "2.4rem" }} className={step.state === "failed" ? "down" : ""}>
                    {step.note}
                  </small>
                </span>
                {step.action !== null && (
                  <button
                    type="button"
                    className="btn small go"
                    disabled={busy}
                    onClick={() => press(step.action as NonNullable<Step["action"]>)}
                  >
                    {BUTTON[step.action]}
                  </button>
                )}
                {index === steps.length - 1 && (
                  <button type="button" className="btn small" disabled>
                    Deploy…
                  </button>
                )}
              </div>
            ))}
          </div>
          {problem !== null && (
            <p className="down" role="alert">
              {problem}
            </p>
          )}
          <p className="soft" style={{ fontSize: "var(--t-s)" }}>
            Most strategies from videos fail step 4, the <Term word="luck" />. Finding that out here costs
            nothing.
          </p>
        </div>
      </div>
    </>
  );
}
