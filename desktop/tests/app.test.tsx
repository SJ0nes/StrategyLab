import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";
import type { LatestSearch, Overview } from "../src/api";
import { accountLabel } from "../src/components/TopBar";
import { gateLine } from "../src/pages/Dashboard";

const FAILED: LatestSearch = {
  artifact: "search-sample.json",
  generated_at: "2026-01-01T00:00:00+00:00",
  cells: 90,
  candidate: { fast: 10, slow: 80, trend: 150 },
  sharpe: 0.64,
  max_drawdown_pct: 19.4,
  gate: { status: "failed", beaten: 25, required: 95, scoreable_trials: 100, summary: "sample" },
  config_changed_since: false,
};

const OVERVIEW: Overview = {
  protocol_version: 1,
  held_back_reads: 0,
  prices: {
    available: true,
    instrument: "SPY",
    bars: 5033,
    first_bar: "2005-01-03",
    last_bar: "2024-12-31",
    content_hash: "sample",
  },
  kill_switch: false,
  venue: { environment: null, key_present: false },
  strategies: [
    {
      id: "ma_crossover",
      name: "ma_crossover",
      description: "Trend following.",
      config: "configs/ma_crossover.yaml",
      state: "draft",
      latest_search: FAILED,
    },
  ],
  unreadable_configs: [],
};

function answerWith(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  window.location.hash = "";
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the luck test line", () => {
  it("states a failure with the count it reached and the count it needed", () => {
    expect(gateLine(FAILED)).toEqual({
      tone: "bad",
      text: "Failed the luck test: beat 25 of 100, needed 95.",
    });
  });

  it("states a pass", () => {
    const passed = { ...FAILED, gate: { ...FAILED.gate, status: "passed" as const, beaten: 97 } };
    expect(gateLine(passed)).toEqual({ tone: "good", text: "Passed the luck test: beat 97 of 100." });
  });

  it("does not call too few trials a pass or a failure", () => {
    const few = { ...FAILED, gate: { ...FAILED.gate, status: "undetermined" as const, scoreable_trials: 2 } };
    expect(gateLine(few).tone).toBe("warn");
    expect(gateLine(few).text).toContain("only 2 trials");
  });

  it("says so when there is no search at all", () => {
    expect(gateLine(null)).toEqual({ tone: "", text: "No search has been run for this strategy." });
  });
});

describe("the account tag", () => {
  it("never claims a practice account without a key and the demo setting", () => {
    expect(accountLabel({ environment: null, key_present: false })).toBe("No venue key set");
    expect(accountLabel({ environment: "demo", key_present: false })).toBe("No venue key set");
    expect(accountLabel({ environment: "live", key_present: true })).toBe("Venue not set to practice");
    expect(accountLabel({ environment: "demo", key_present: true })).toBe("Practice account");
  });
});

describe("the app", () => {
  it("asks the server for the overview and attaches no credential of its own", async () => {
    const fetchMock = answerWith(OVERVIEW);
    render(<App />);
    await screen.findByRole("heading", { name: /ma_crossover/ });

    // The token is added by the Electron main process, outside the page.
    expect(fetchMock).toHaveBeenCalledWith("/api/overview");
    expect("strategylab" in window).toBe(false);
  });

  it("shows the strategy as a draft with its real luck-test result", async () => {
    answerWith(OVERVIEW);
    render(<App />);

    expect(await screen.findByText("Failed the luck test: beat 25 of 100, needed 95.")).toBeInTheDocument();
    expect(screen.getByText("fast 10, slow 80, trend 150")).toBeInTheDocument();
    expect(screen.getByText("0.64")).toBeInTheDocument();
    expect(screen.getByText("No venue key set")).toBeInTheDocument();
    expect(screen.getByText(/Prices stored to 2024-12-31/)).toBeInTheDocument();
  });

  it("shows no figure where there is none", async () => {
    const empty: Overview = {
      ...OVERVIEW,
      prices: { available: false, reason: "No price data has been fetched." },
      strategies: [{ ...OVERVIEW.strategies[0]!, latest_search: null }],
    };
    answerWith(empty);
    render(<App />);

    expect(await screen.findByText("No search has been run for this strategy.")).toBeInTheDocument();
    expect(screen.getByText("No price data")).toBeInTheDocument();
    expect(screen.getAllByText("none")).toHaveLength(2);
  });

  it("says a section is not built instead of drawing an empty one", async () => {
    window.location.hash = "#trades";
    answerWith(OVERVIEW);
    render(<App />);

    expect(await screen.findByText("Not built yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Trades" })).toHaveAttribute("aria-current", "page");
  });

  it("says what went wrong when the server refuses it", async () => {
    answerWith({ detail: "no" }, 401);
    render(<App />);

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("refused the request"));
  });

  it("says so when the project has no strategy file", async () => {
    answerWith({ ...OVERVIEW, strategies: [] });
    render(<App />);

    expect(await screen.findByText("No strategy found")).toBeInTheDocument();
  });
});
