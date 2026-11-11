import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App, JOB_POLL_MS } from "../src/App";
import type { Job, Jobs, Overview } from "../src/api";
import { JobDialog, jobLine, jobPercent } from "../src/components/JobDialog";
import { jobLabel } from "../src/components/TopBar";

const RUNNING: Job = {
  id: "20260301T120000Z-luck_test",
  kind: "luck_test",
  strategy_id: "ma_crossover",
  trials: 100,
  started_at: "2026-03-01T12:00:00+00:00",
  status: "running",
  progress: { finished: 37, total: 100 },
  last_line: "surrogate 37 of 100 done",
};

const IDLE: Jobs = { current: null, recent: [] };

/** Record each request and answer it from a table of "METHOD path". */
function serve(routes: Record<string, { status: number; body: unknown }>): string[] {
  const asked: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${path}`;
      asked.push(key);
      const answer = routes[key] ?? { status: 404, body: { detail: "not found" } };
      return new Response(JSON.stringify(answer.body), { status: answer.status });
    }),
  );
  return asked;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("a job in words", () => {
  it("says how far a luck test has got", () => {
    expect(jobLine(RUNNING)).toBe("Running. 37 of 100 scrambled histories done.");
    expect(jobPercent(RUNNING)).toBe(37);
    expect(jobLabel(RUNNING)).toBe("Luck test running: 37 of 100");
  });

  it("claims no progress it was not told of", () => {
    const early = { ...RUNNING, progress: null };
    const search: Job = { ...early, kind: "search" };

    expect(jobPercent(early)).toBeNull();
    expect(jobLine(early)).toContain("first search is under way");
    expect(jobLine(search)).toContain("reports no progress");
    expect(jobLabel(search)).toBe("Search running");
  });

  it("says a stopped or lost job wrote no result, and quotes a failed job's last line", () => {
    expect(jobLine({ ...RUNNING, status: "stopped" })).toBe(
      "Stopped as asked. 37 of 100 scrambled histories done. No result was written.",
    );
    expect(jobLine({ ...RUNNING, status: "lost" })).toContain("No result was written");
    expect(jobLine({ ...RUNNING, status: "failed", last_line: "SPY is not cached" })).toContain("SPY is not cached");
    expect(jobLine({ ...RUNNING, status: "stopping" })).toContain("finish first");
    expect(jobLabel({ ...RUNNING, status: "stopping" })).toBe("Luck test stopping: 37 of 100");
  });

  it("fills the bar only when a job finished, not when it stopped", () => {
    expect(jobPercent({ ...RUNNING, status: "finished", progress: null })).toBe(100);
    expect(jobPercent({ ...RUNNING, status: "stopped" })).toBe(37);
    expect(jobPercent({ ...RUNNING, status: "failed", progress: null })).toBeNull();
  });
});

describe("the job dialog", () => {
  const props = { strategyId: "ma_crossover", onChanged: vi.fn(), onClose: vi.fn() };

  afterEach(() => {
    props.onChanged.mockReset();
    props.onClose.mockReset();
  });

  it("starts nothing by being opened", () => {
    const asked = serve({});
    render(<JobDialog {...props} kind="luck_test" jobs={IDLE} />);

    expect(screen.getByRole("status")).toHaveTextContent("Ready.");
    expect(screen.getByText(/about an hour/)).toBeInTheDocument();
    expect(asked).toEqual([]);
  });

  it("cannot start before it knows whether something is running", () => {
    serve({});
    render(<JobDialog {...props} kind="search" jobs={null} />);

    expect(screen.getByRole("button", { name: "Start" })).toBeDisabled();
  });

  it("starts the kind it was opened for, once, when Start is pressed", async () => {
    const asked = serve({
      "POST /api/strategies/ma_crossover/jobs/luck_test": { status: 201, body: { ...RUNNING, progress: null } },
    });
    render(<JobDialog {...props} kind="luck_test" jobs={IDLE} />);

    fireEvent.click(screen.getByRole("button", { name: "Start" }));

    await waitFor(() => expect(props.onChanged).toHaveBeenCalledTimes(1));
    expect(asked).toEqual(["POST /api/strategies/ma_crossover/jobs/luck_test"]);
  });

  it("shows the server's own words when it refuses", async () => {
    serve({
      "POST /api/strategies/ma_crossover/jobs/search": {
        status: 409,
        body: { detail: "a job is already running: 20260301T120000Z-luck_test" },
      },
    });
    render(<JobDialog {...props} kind="search" jobs={IDLE} />);

    fireEvent.click(screen.getByRole("button", { name: "Start" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Refused: a job is already running: 20260301T120000Z-luck_test.",
    );
  });

  it("shows a running job and offers to stop it, not to start another", () => {
    serve({});
    render(<JobDialog {...props} kind="luck_test" jobs={{ current: RUNNING, recent: [RUNNING] }} />);

    expect(screen.getByRole("status")).toHaveTextContent("37 of 100 scrambled histories done");
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "37");
    expect(screen.queryByRole("button", { name: "Start" })).toBeNull();
    expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Close (it keeps running)" })).toBeInTheDocument();
  });

  it("asks the server to stop that job when Stop is pressed", async () => {
    const asked = serve({
      "POST /api/jobs/20260301T120000Z-luck_test/stop": { status: 200, body: { ...RUNNING, status: "stopping" } },
    });
    render(<JobDialog {...props} kind="luck_test" jobs={{ current: RUNNING, recent: [RUNNING] }} />);

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));

    await waitFor(() => expect(props.onChanged).toHaveBeenCalledTimes(1));
    expect(asked).toEqual(["POST /api/jobs/20260301T120000Z-luck_test/stop"]);
  });

  it("offers no Stop for a plain search, which cannot be stopped", () => {
    serve({});
    const search: Job = { ...RUNNING, kind: "search", progress: null };
    render(<JobDialog {...props} kind="search" jobs={{ current: search, recent: [search] }} />);

    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Start" })).toBeNull();
  });

  it("says so when the job running is not the kind that was asked for", () => {
    serve({});
    render(<JobDialog {...props} kind="search" jobs={{ current: RUNNING, recent: [RUNNING] }} />);

    expect(screen.getByText(/Another job is already running/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Run the luck test" })).toBeInTheDocument();
  });

  it("closes without asking the server for anything", () => {
    const asked = serve({});
    render(<JobDialog {...props} kind="luck_test" jobs={{ current: RUNNING, recent: [RUNNING] }} />);

    fireEvent.click(screen.getByRole("button", { name: "Close (it keeps running)" }));

    expect(props.onClose).toHaveBeenCalledTimes(1);
    expect(asked).toEqual([]);
  });
});

describe("the app, while a job runs", () => {
  const OVERVIEW: Overview = {
    protocol_version: 1,
    held_back_reads: 0,
    prices: { available: false, reason: "sample" },
    kill_switch: false,
    venue: { environment: null, key_present: false },
    strategies: [
      {
        id: "ma_crossover",
        name: "ma_crossover",
        description: "Trend following.",
        config: "configs/ma_crossover.yaml",
        state: "draft",
        latest_search: null,
      },
    ],
    unreadable_configs: [],
  };

  afterEach(() => {
    vi.useRealTimers();
    window.location.hash = "";
  });

  it("shows the running job in the top bar and opens it when pressed", async () => {
    serve({
      "GET /api/overview": { status: 200, body: OVERVIEW },
      "GET /api/jobs": { status: 200, body: { current: RUNNING, recent: [RUNNING] } },
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Luck test running: 37 of 100/ }));

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("37 of 100 scrambled histories done");
  });

  it("shows no job button when nothing is running", async () => {
    serve({
      "GET /api/overview": { status: 200, body: OVERVIEW },
      "GET /api/jobs": { status: 200, body: IDLE },
    });
    render(<App />);
    await screen.findByRole("heading", { name: /ma_crossover/ });

    expect(screen.queryByRole("button", { name: /running/ })).toBeNull();
  });

  it("reads the project again when a running job ends, and not before", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const routes = {
      "GET /api/overview": { status: 200, body: OVERVIEW },
      "GET /api/jobs": { status: 200, body: { current: RUNNING, recent: [RUNNING] } as Jobs },
    };
    const asked = serve(routes);
    const overviews = () => asked.filter((request) => request === "GET /api/overview").length;
    render(<App />);
    await screen.findByRole("button", { name: /Luck test running/ });
    expect(overviews()).toBe(1);

    await act(() => vi.advanceTimersByTimeAsync(JOB_POLL_MS));
    expect(overviews()).toBe(1);

    const finished: Job = { ...RUNNING, status: "finished" };
    routes["GET /api/jobs"].body = { current: null, recent: [finished] };
    await act(() => vi.advanceTimersByTimeAsync(JOB_POLL_MS));

    await waitFor(() => expect(overviews()).toBe(2));
    expect(screen.queryByRole("button", { name: /Luck test running/ })).toBeNull();
  });
});
