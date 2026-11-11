import { useState } from "react";

import { ApiError, type Strategy, takeExam } from "../api";

/** What the operator types to take the final exam. The server checks it again. */
export const EXAM_PHRASE = "SPEND ONE LOOK";

interface Props {
  strategy: Strategy;
  /** How many times the held-back years have been read already. */
  looks: number;
  onStarted: () => void;
  onClose: () => void;
}

/**
 * Take the final exam: one permanent read of the years the strategy never saw.
 *
 * The phrase is not the strategy's name, which is what deploying asks for, so
 * that the habit of one cannot do the other.
 */
export function ExamDialog({ strategy, looks, onStarted, onClose }: Props) {
  const [typed, setTyped] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function go() {
    setBusy(true);
    setProblem(null);
    takeExam(strategy.id, typed)
      .then(() => onStarted())
      .catch((error: unknown) =>
        setProblem(error instanceof ApiError ? error.message : "The exam could not be started."),
      )
      .finally(() => setBusy(false));
  }

  return (
    <div className="modal open" role="dialog" aria-modal="true" aria-labelledby="exam-title">
      <div className="dialog">
        <h2 id="exam-title" style={{ fontSize: "var(--t-l)" }}>
          Final exam for {strategy.name}
        </h2>
        <p>
          It tests the version the last search put forward on 2019 to 2024, the years kept back from every search.
          The result is final: a strategy that fails cannot be tuned and tested on those years again without the
          test meaning less.
        </p>
        <div className="rule" style={{ borderColor: "var(--warn-rule)", background: "var(--warn-bg)" }}>
          <b>Each look is counted and permanent.</b> Looks used so far: {looks}. This makes {looks + 1}. It cannot be
          stopped once it starts.
        </div>
        <p>
          Type <b className="mono">{EXAM_PHRASE}</b> to confirm.
        </p>
        <input
          type="text"
          value={typed}
          autoComplete="off"
          aria-label="Confirmation phrase"
          onChange={(event) => setTyped(event.target.value)}
        />
        {problem !== null && (
          <p className="down" role="alert">
            {problem}
          </p>
        )}
        <div className="between" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn go" disabled={busy || typed.trim() !== EXAM_PHRASE} onClick={go}>
            Take the final exam
          </button>
        </div>
      </div>
    </div>
  );
}
