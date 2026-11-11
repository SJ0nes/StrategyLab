import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, type JobKind, type Jobs, type Overview, getJobs, getOverview, markTourSeen } from "./api";
import { JobDialog } from "./components/JobDialog";
import { Tour, Welcome, confetti } from "./components/Tour";
import { Nav, SECTIONS, type SectionId } from "./components/Nav";
import { TopBar } from "./components/TopBar";
import { Backtest } from "./pages/Backtest";
import { Dashboard } from "./pages/Dashboard";
import { Improve } from "./pages/Improve";
import { NotBuilt } from "./pages/NotBuilt";
import { Tests } from "./pages/Tests";

/** How often the window asks what is running, in milliseconds. */
export const JOB_POLL_MS = 3000;

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
              <Dashboard
                strategy={strategy}
                onStart={setDialog}
                onChanged={() => setChanged((count) => count + 1)}
              />
              {overview.unreadable_configs.map((file) => (
                <div className="empty" key={file.config}>
                  <b>
                    <span className="mono">{file.config}</span> could not be read as a strategy
                  </b>
                  {file.reason}
                </div>
              ))}
            </>
          ) : section === "tests" ? (
            <Tests overview={overview} strategy={strategy} key={`${strategy.id}-${ended}`} />
          ) : section === "backtest" ? (
            <Backtest strategy={strategy} key={`${strategy.id}-${ended}`} onStart={setDialog} />
          ) : section === "improve" ? (
            <Improve strategy={strategy} key={`${strategy.id}-${ended}`} onStart={setDialog} />
          ) : (
            <NotBuilt section={section} />
          )}
        </div>
      </main>
      {tour === "welcome" && <Welcome onStart={() => setTour("touring")} onSkip={() => endTour(false)} />}
      {tour === "touring" && <Tour onGo={go} onEnd={endTour} />}
      <canvas id="confetti" ref={confettiRef} hidden />
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
