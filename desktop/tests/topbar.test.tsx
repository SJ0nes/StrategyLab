import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App, firstDraft, openingStrategy } from "../src/App";
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
  settlement: { state: "agree" },
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

describe("which strategy the window opens on", () => {
  const all = OVERVIEW.strategies;

  it("is the one chosen, else the first deployed, else the first", () => {
    expect(openingStrategy(all, "beta", STATUS)?.id).toBe("beta");
    expect(openingStrategy(all, null, { ...STATUS, deployed: [{ strategy_id: "gamma", paused: false, proven: false }] })?.id).toBe("gamma");
    expect(openingStrategy(all, null, { ...STATUS, deployed: [] })?.id).toBe("alpha");
    expect(openingStrategy(all, null, null)?.id).toBe("alpha");
    expect(openingStrategy(all, "gone", { ...STATUS, deployed: [{ strategy_id: "missing", paused: false, proven: false }] })?.id).toBe("alpha");
    expect(openingStrategy([], null, STATUS)).toBeUndefined();
  });
});

describe("the draft the tour shows", () => {
  it("is the first strategy not deployed, or none when all are", () => {
    expect(firstDraft(OVERVIEW.strategies, STATUS)?.id).toBe("beta");
    expect(firstDraft(OVERVIEW.strategies, null)?.id).toBe("alpha");
    const all = { ...STATUS, deployed: ["alpha", "beta", "gamma"].map((id) => ({ strategy_id: id, paused: false, proven: false })) };
    expect(firstDraft(OVERVIEW.strategies, all)).toBeUndefined();
  });
});

describe("the app", () => {
  it("keeps the strategy it opened on when a later deployment answer fails", async () => {
    const onlyGamma: DeploymentStatus = { ...STATUS, deployed: [{ strategy_id: "gamma", paused: true, proven: false }] };
    const asked: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        asked.push(path);
        if (path === "/api/deployment?strategy_id=gamma") {
          return new Response("{}", { status: 500 });
        }
        const body = path === "/api/overview" ? OVERVIEW : path.startsWith("/api/deployment") ? onlyGamma : { current: null, recent: [] };
        return new Response(JSON.stringify(body));
      }),
    );

    render(<App />);

    await waitFor(() => expect(asked).toContain("/api/deployment?strategy_id=gamma"));
    await new Promise((resolve) => setTimeout(resolve, 200));
    // Without the choice kept, the failed answer sends the window back to alpha,
    // whose answer sends it to gamma again, and so on.
    expect(asked).not.toContain("/api/deployment?strategy_id=alpha");
    expect(screen.getByRole("combobox")).toHaveValue("gamma");
  });

  it("shows a draft for the tour's stops about drafts, then the strategy shown before", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        const body = path === "/api/overview" ? OVERVIEW : path.startsWith("/api/deployment") ? STATUS : { current: null, recent: [] };
        return new Response(JSON.stringify(body));
      }),
    );
    render(<App />);
    const picker = await screen.findByRole("combobox");
    await waitFor(() => expect(screen.getByText("Running")).toBeInTheDocument());
    expect(picker).toHaveValue("alpha");

    fireEvent.click(screen.getByRole("button", { name: /Tour/ }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("beta"));

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("alpha"));
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("beta"));
    fireEvent.click(screen.getByRole("button", { name: "Skip" }));
    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("alpha"));
  });

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

  it("moves to a deployed strategy when the first on the list is a draft", async () => {
    const onlyGamma: DeploymentStatus = { ...STATUS, deployed: [{ strategy_id: "gamma", paused: true, proven: false }] };
    const asked: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        asked.push(path);
        const body = path === "/api/overview" ? OVERVIEW : path.startsWith("/api/deployment") ? onlyGamma : { current: null, recent: [] };
        return new Response(JSON.stringify(body));
      }),
    );

    render(<App />);

    await waitFor(() => expect(asked).toContain("/api/deployment?strategy_id=gamma"));
    expect(await screen.findByText("Paused")).toBeInTheDocument();
  });
});
