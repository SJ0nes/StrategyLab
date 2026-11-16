import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";
import type { DeploymentStatus, Overview, Strategy } from "../src/api";
import { TopBar } from "../src/components/TopBar";

function strategy(id: string): Strategy {
  return {
    id,
    name: id,
    description: "Fixture.",
    config: `configs/${id}.yaml`,
    config_hash: "a".repeat(64),
    state: "draft",
    latest_search: null,
    rules: { entry: [], exit: [], search: [], fixed: [], versions: 0 },
    source: null,
    confirmed: false,
    confirmed_at: null,
    stale: false,
  };
}

const OVERVIEW: Overview = {
  protocol_version: 1,
  held_back_reads: 0,
  prices: { available: false, reason: "No price data has been fetched." },
  kill_switch: false,
  venue: { environment: "demo", key_present: true },
  strategies: [strategy("alpha"), strategy("beta"), strategy("gamma")],
  unreadable_configs: [],
  tour_seen: true,
};

const STATUS: DeploymentStatus = {
  deployment: null,
  deployed: [
    { strategy_id: "alpha", paused: false, proven: false },
    { strategy_id: "gamma", paused: true, proven: false },
  ],
  stop_all: { stopped: false, stopped_at: null, damaged: false },
  venue_unready: null,
  schedule: null,
  live: null,
};

function bar(chosen: string, status: DeploymentStatus | null = STATUS) {
  return render(
    <TopBar
      overview={OVERVIEW}
      strategy={OVERVIEW.strategies.find((item) => item.id === chosen)}
      onChoose={() => undefined}
      job={null}
      onOpenJob={() => undefined}
      onTour={() => undefined}
      deployment={status}
      onStopAll={() => undefined}
    />,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the strategy list with several deployed", () => {
  it("lists every deployed strategy under Deployed and the rest under Drafts", () => {
    bar("alpha");

    const deployed = screen.getByRole("group", { name: "Deployed" });
    const drafts = screen.getByRole("group", { name: "Drafts, not deployed" });
    expect(within(deployed).getAllByRole("option").map((option) => option.textContent)).toEqual(["alpha", "gamma"]);
    expect(within(drafts).getAllByRole("option").map((option) => option.textContent)).toEqual(["beta"]);
  });

  it("says whether the chosen strategy runs, is paused, or is a draft", () => {
    const { unmount } = bar("alpha");
    expect(screen.getByText("Running")).toBeInTheDocument();
    unmount();

    const paused = bar("gamma");
    expect(screen.getByText("Paused")).toBeInTheDocument();
    paused.unmount();

    bar("beta");
    expect(screen.getByText("Draft")).toBeInTheDocument();
  });

  it("shows no Deployed group when nothing is deployed, or nothing could be read", () => {
    const { unmount } = bar("alpha", { ...STATUS, deployed: [] });
    expect(screen.queryByRole("group", { name: "Deployed" })).toBeNull();
    unmount();

    bar("alpha", null);
    expect(screen.queryByRole("group", { name: "Deployed" })).toBeNull();
  });
});

describe("the app", () => {
  it("asks for the deployment of the strategy shown", async () => {
    const asked: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        asked.push(path);
        const body = path === "/api/overview" ? OVERVIEW : path.startsWith("/api/deployment") ? STATUS : { current: null, recent: [] };
        return new Response(JSON.stringify(body));
      }),
    );

    render(<App />);

    await waitFor(() => expect(asked).toContain("/api/deployment?strategy_id=alpha"));
  });
});
