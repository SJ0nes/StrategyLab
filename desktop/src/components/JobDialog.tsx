import { useState } from "react";

import { ApiError, type Job, type JobKind, type Jobs, startJob, stopJob } from "../api";

/** What each kind of job is called, what it does, and how long it takes. */
export const JOB_WORDS: Record<JobKind, { title: string; does: string; takes: string }> = {
  search: {
    title: "Search for better settings",
    does: "Replays every version of the strategy on old prices and records them all. It runs no luck test, so the result cannot be deployed.",
    takes: "Takes a few minutes. It cannot be stopped once started.",
  },
  luck_test: {
    title: "Run the luck test",
    does: "Runs the search again, then repeats it on 100 scrambled price histories to see whether the best version beats luck.",
    takes: "Takes about an hour on this computer, longer on a slower one. It keeps running if you close the app.",
  },
};

/** One sentence on where a job has got to. */
export function jobLine(job: Job): string {
  const done = job.progress === null ? "" : ` ${job.progress.finished} of ${job.progress.total} scrambled histories done.`;
  switch (job.status) {
    case "running":
      return job.kind === "search"
        ? "Running. A search without a luck test reports no progress until it ends."
        : `Running.${done === "" ? " The first search is under way; scrambled histories come next." : done}`;
    case "stopping":
      return `Stopping.${done} The histories already being worked on finish first, which can take ten minutes or more.`;
    case "finished":
      return "Finished. The result is on Improve, Backtest and Tests.";
    case "stopped":
      return `Stopped as asked.${done} No result was written.`;
    case "failed":
      return `It did not finish. Its last line was: ${job.last_line ?? "nothing"}`;
    case "lost":
      return "It stopped without saying why. The computer may have been restarted. No result was written.";
  }
}

/** How full the bar is, from 0 to 100; null when there is nothing to measure. */
export function jobPercent(job: Job): number | null {
  if (job.status === "finished") {
    return 100;
  }
  if (job.progress === null || job.progress.total <= 0) {
    return null;
  }
  return Math.round((job.progress.finished / job.progress.total) * 100);
}

interface Props {
  strategyId: string;
  kind: JobKind;
  /** The server's latest word on jobs; null before the first answer. */
  jobs: Jobs | null;
  /** Ask for the server's word again, now. */
  onChanged: () => void;
  onClose: () => void;
}

/** Start a job, or watch the one that is running. Closing the dialog stops nothing. */
export function JobDialog({ strategyId, kind, jobs, onChanged, onClose }: Props) {
  const [started, setStarted] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const running = jobs?.current ?? null;
  // The job this dialog started, once it has ended, so its outcome stays on screen.
  const mine = started === null ? null : (jobs?.recent.find((job) => job.id === started) ?? null);
  const shown = running ?? mine;
  const words = JOB_WORDS[shown?.kind ?? kind];
  const percent = shown === null ? null : jobPercent(shown);

  function act(request: () => Promise<Job>) {
    setBusy(true);
    setProblem(null);
    request()
      .then((job) => setStarted(job.id))
      .catch((error: unknown) =>
        setProblem(error instanceof ApiError ? error.message : "The request could not be made."),
      )
      .finally(() => {
        setBusy(false);
        onChanged();
      });
  }

  return (
    <div className="modal open" role="dialog" aria-modal="true" aria-labelledby="dlg-title">
      <div className="dialog">
        <h2 id="dlg-title" style={{ fontSize: "var(--t-l)" }}>
          {words.title}
        </h2>
        <p className="soft">
          {words.does} {words.takes}
        </p>
        <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent ?? undefined}>
          <i style={{ width: `${percent ?? 0}%` }} />
        </div>
        <p className="soft" role="status">
          {jobs === null ? "Checking whether anything is running…" : shown === null ? "Ready." : jobLine(shown)}
        </p>
        {running !== null && running.kind !== kind && started === null && (
          <p className="soft">Another job is already running, shown above. Only one runs at a time.</p>
        )}
        {problem !== null && (
          <p className="down" role="alert">
            {problem}
          </p>
        )}
        <div className="between" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>
            {running === null ? "Close" : "Close (it keeps running)"}
          </button>
          {running !== null && running.kind === "luck_test" && (
            <button
              type="button"
              className="btn"
              disabled={busy || running.status === "stopping"}
              onClick={() => act(() => stopJob(running.id))}
            >
              Stop
            </button>
          )}
          {running === null && mine === null && (
            <button
              type="button"
              className="btn primary"
              disabled={busy || jobs === null}
              onClick={() => act(() => startJob(strategyId, kind))}
            >
              Start
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
