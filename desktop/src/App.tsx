import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  type DeploymentStatus,
  type JobKind,
  type Jobs,
  type Overview,
  getDeployment,
  getJobs,
  getOverview,
  markTourSeen,
  setStopAll,
} from "./api";
import { DeployDialog } from "./components/DeployDialog";
import { JobDialog } from "./components/JobDialog";
import { Tour, Welcome, confetti } from "./components/Tour";
import { Nav, SECTIONS, type SectionId } from "./components/Nav";
import { TopBar } from "./components/TopBar";
import { Backtest } from "./pages/Backtest";
import { Compare } from "./pages/Compare";
import { Dashboard } from "./pages/Dashboard";
import { Deployed } from "./pages/Deployed";
import { Improve } from "./pages/Improve";
import { Tests } from "./pages/Tests";
import { Trades } from "./pages/Trades";

/** How often the window asks what is running, in milliseconds. */
export const JOB_POLL_MS = 3000;

/** How often the window asks for the chosen strategy's deployment, in milliseconds. */
export const DEPLOYMENT_POLL_MS = 30000;

/** Why a strategy cannot be deployed now, from what the server says; null when it can. */
export function deployBlocker(status: DeploymentStatus | null, strategyId: string): string | null {
  if (status === null) {
    return "the deployment could not be read";
  }
  const { deployment } = status;
  if (deployment !== null) {
    if (!deployment.available) {
      return "the deployment record is damaged and cannot be read";
    }
    if (deployment.strategy_id === strategyId) {
      return "it is already deployed";
    }
  }
  return status.venue_unready;
}

/** The section named in the address bar, or the dashboard. */
function sectionFromHash(hash: string): SectionId {
  const wanted = hash.replace("#", "");
  return SECTIONS.find((section) => section.id === wanted)?.id ?? "dashboard";
}

type Load =
  | { state: "loading" }
  | { state: "failed"; message: string }
  | { state: "ready"; overview: Overview };

export function App() {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [section, setSection] = useState<SectionId>(() => sectionFromHash(window.location.hash));
  const [chosen, setChosen] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Jobs | null>(null);
  const [dialog, setDialog] = useState<JobKind | null>(null);
  // Counts the jobs seen to end. Pages are keyed on it, so each reads the
  // project again when a job has written something new.
  const [ended, setEnded] = useState(0);
  // Counts the changes made from this window that are not jobs: a confirmation.
  const [changed, setChanged] = useState(0);
  const wasRunning = useRef(false);
  // "welcome" offers the tour, "touring" runs it, null shows neither.
  const [tour, setTour] = useState<"welcome" | "touring" | null>(null);
  const offered = useRef(false);
  const confettiRef = useRef<HTMLCanvasElement>(null);
  const [deployment, setDeployment] = useState<DeploymentStatus | null>(null);
  const [deploying, setDeploying] = useState(false);
  const [allowing, setAllowing] = useState(false);

  useEffect(() => {
    getOverview()
      .then((overview) => setLoad({ state: "ready", overview }))
      .catch((error: unknown) =>
        setLoad({
          state: "failed",
          message: error instanceof ApiError ? error.message : "The app could not read the project.",
        }),
      );
  }, [ended, changed]);

  // The strategy whose deployment is asked for: the one chosen, or the first.
  const shownId =
    load.state === "ready"
      ? ((load.overview.strategies.find((candidate) => candidate.id === chosen) ?? load.overview.strategies[0])?.id ?? null)
      : null;

  const askDeployment = useCallback(() => {
    getDeployment(shownId)
      // Shown only if it is what the server sends for this route; anything else
      // reads as "could not be read", which keeps every control off.
      .then((status) =>
        setDeployment(
          typeof status === "object" && status !== null && "stop_all" in status && Array.isArray(status.deployed)
            ? status
            : null,
        ),
      )
      .catch(() => setDeployment(null));
  }, [shownId]);

  useEffect(() => {
    askDeployment();
    const timer = window.setInterval(askDeployment, DEPLOYMENT_POLL_MS);
    return () => window.clearInterval(timer);
  }, [askDeployment, ended, changed]);

  const askJobs = useCallback(() => {
    getJobs()
      .then((answer) => {
        if (wasRunning.current && answer.current === null) {
          setEnded((count) => count + 1);
        }
        wasRunning.current = answer.current !== null;
        setJobs(answer);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    askJobs();
    const timer = window.setInterval(askJobs, JOB_POLL_MS);
    return () => window.clearInterval(timer);
  }, [askJobs]);

  useEffect(() => {
    if (load.state === "ready" && !load.overview.tour_seen && !offered.current) {
      offered.current = true;
      setTour("welcome");
    }
  }, [load]);

  const go = useCallback((target: SectionId) => {
    window.location.hash = target;
    setSection(target);
  }, []);

  const endTour = useCallback((finished: boolean) => {
    setTour(null);
    go("dashboard");
    if (finished) {
      confetti(confettiRef.current);
    }
    // Remembered by the server: the page's own storage is lost with each launch's port.
    markTourSeen().catch(() => undefined);
  }, [go]);

  useEffect(() => {
    const onHashChange = () => setSection(sectionFromHash(window.location.hash));
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  if (load.state === "loading" && ended === 0 && changed === 0) {
    return <p className="soft" style={{ padding: "2rem" }}>Reading the project…</p>;
  }
  if (load.state === "failed") {
    return (
      <div className="empty" role="alert" style={{ margin: "2rem" }}>
        <b>The app could not start</b>
        {load.message}
      </div>
    );
  }

  if (load.state !== "ready") {
    return null;
  }
  const { overview } = load;
  const strategy =
    overview.strategies.find((candidate) => candidate.id === chosen) ?? overview.strategies[0];

  return (
    <div className="app">
      <TopBar
        overview={overview}
        strategy={strategy}
        onChoose={setChosen}
        job={jobs?.current ?? null}
        onOpenJob={setDialog}
        onTour={() => setTour("touring")}
        deployment={deployment}
        onStopAll={() => {
          if (deployment?.stop_all.stopped) {
            setAllowing(true);
          } else {
            setStopAll(true)
              .catch(() => undefined)
              .finally(() => setChanged((count) => count + 1));
          }
        }}
      />
      <Nav current={section} />
      <main id="scroll">
        <div className="wrap">
          {strategy === undefined ? (
            <div className="empty">
              <b>No strategy found</b>
              The project's <span className="mono">configs/</span> folder holds no strategy file.
            </div>
          ) : section === "dashboard" ? (
            <>
              {deployment?.deployment != null &&
              deployment.deployment.available &&
              deployment.deployment.strategy_id === strategy.id ? (
                <Deployed
                  strategy={strategy}
                  deployed={deployment.deployment}
                  status={deployment}
                  onStart={setDialog}
                  onChanged={() => setChanged((count) => count + 1)}
                />
              ) : (
                <Dashboard
                  strategy={strategy}
                  onStart={setDialog}
                  onChanged={() => setChanged((count) => count + 1)}
                  blocker={deployBlocker(deployment, strategy.id)}
                  onDeploy={() => setDeploying(true)}
                />
              )}
              {overview.unreadable_configs.map((file) => (
                <div className="empty" key={file.config}>
                  <b>
                    <span className="mono">{file.config}</span> could not be read as a strategy
                  </b>
                  {file.reason}
                </div>
              ))}
            </>
          ) : section === "trades" ? (
            <Trades strategy={strategy} status={deployment} />
          ) : section === "tests" ? (
            <Tests
              overview={overview}
              strategy={strategy}
              key={`${strategy.id}-${ended}-${changed}`}
              onExamStarted={() => {
                askJobs();
                setDialog("final_exam");
              }}
            />
          ) : section === "backtest" ? (
            <Backtest strategy={strategy} key={`${strategy.id}-${ended}`} onStart={setDialog} />
          ) : section === "improve" ? (
            <Improve strategy={strategy} key={`${strategy.id}-${ended}`} onStart={setDialog} />
          ) : (
            <Compare strategies={overview.strategies} key={`compare-${ended}`} />
          )}
        </div>
      </main>
      {tour === "welcome" && <Welcome onStart={() => setTour("touring")} onSkip={() => endTour(false)} />}
      {tour === "touring" && <Tour onGo={go} onEnd={endTour} />}
      <canvas id="confetti" ref={confettiRef} hidden />
      {deploying && strategy !== undefined && (
        <DeployDialog
          strategy={strategy}
          onClose={() => setDeploying(false)}
          onDone={() => {
            setDeploying(false);
            setChanged((count) => count + 1);
          }}
        />
      )}
      {allowing && (
        <div className="modal open" role="dialog" aria-modal="true" aria-labelledby="allow-title">
          <div className="dialog">
            <h2 id="allow-title" style={{ fontSize: "var(--t-l)" }}>
              Allow trading again?
            </h2>
            <p>The deployed strategy's daily check runs again from the next weekday after the close.</p>
            <div className="between" style={{ justifyContent: "flex-end" }}>
              <button type="button" className="btn" onClick={() => setAllowing(false)}>
                Keep stopped
              </button>
              <button
                type="button"
                className="btn go"
                onClick={() => {
                  setAllowing(false);
                  setStopAll(false)
                    .catch(() => undefined)
                    .finally(() => setChanged((count) => count + 1));
                }}
              >
                Allow trading
              </button>
            </div>
          </div>
        </div>
      )}
      {dialog !== null && strategy !== undefined && (
        <JobDialog
          strategyId={strategy.id}
          kind={dialog}
          jobs={jobs}
          onChanged={askJobs}
          onClose={() => setDialog(null)}
        />
      )}
    </div>
  );
}
