import { useEffect, useState } from "react";

import { ApiError, type Overview, getOverview } from "./api";
import { Nav, SECTIONS, type SectionId } from "./components/Nav";
import { TopBar } from "./components/TopBar";
import { Dashboard } from "./pages/Dashboard";
import { NotBuilt } from "./pages/NotBuilt";

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

  useEffect(() => {
    getOverview()
      .then((overview) => setLoad({ state: "ready", overview }))
      .catch((error: unknown) =>
        setLoad({
          state: "failed",
          message: error instanceof ApiError ? error.message : "The app could not read the project.",
        }),
      );
  }, []);

  useEffect(() => {
    const onHashChange = () => setSection(sectionFromHash(window.location.hash));
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  if (load.state === "loading") {
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

  const { overview } = load;
  const strategy =
    overview.strategies.find((candidate) => candidate.id === chosen) ?? overview.strategies[0];

  return (
    <div className="app">
      <TopBar overview={overview} strategy={strategy} onChoose={setChosen} />
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
              <Dashboard overview={overview} strategy={strategy} />
              {overview.unreadable_configs.map((file) => (
                <div className="empty" key={file.config}>
                  <b>
                    <span className="mono">{file.config}</span> could not be read as a strategy
                  </b>
                  {file.reason}
                </div>
              ))}
            </>
          ) : (
            <NotBuilt section={section} />
          )}
        </div>
      </main>
    </div>
  );
}
