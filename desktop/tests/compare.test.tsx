import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SearchDetail, Strategy } from "../src/api";
import { Compare, better, bestOf } from "../src/pages/Compare";

function strategy(id: string): Strategy {
  return {
    id,
    name: id,
    description: "",
    config: `configs/${id}.yaml`,
    config_hash: "a".repeat(64),
    state: "draft",
    latest_search: null,
    rules: { entry: [], exit: [], search: [], fixed: [], versions: 1 },
    source: null,
    confirmed: false,
    confirmed_at: null,
    stale: false,
  };
}

function detail(sharpe: number, ret: number, fall: number, status: "passed" | "failed"): SearchDetail {
  return {
    artifact: "search-x.json",
    readable: true,
    generated_at: "2026-11-10T18:40:30+00:00",
    candidate: { fast: 10 },
    sharpe,
    max_drawdown_pct: fall,
    gate: { status, beaten: status === "passed" ? 97 : 25, required: 95, scoreable_trials: 100, summary: "" },
    config_changed_since: false,
    available: true,
    instrument: "SPY",
    window: { start: "2005-01-01", end: "2018-12-31", bars: 3523 },
    starting_equity: 10000,
    max_drawdown_ceiling_pct: 25,
    parameters: ["fast"],
    axes: { fast: [10] },
    dates: ["2005-01-03", "2018-12-31"],
    cells: [
      {
        params: { fast: 10 },
        sharpe,
        max_drawdown_pct: fall,
        trade_count: 17,
        plateau_score: 0.5,
        neighbour_count: 1,
        total_return_pct: ret,
        mark: "candidate",
        curve: [10000, 10000 * (1 + ret / 100)],
      },
    ],
    rejections: [],
    ceiling: null,
  } as unknown as SearchDetail;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("comparing", () => {
  it("marks the better side, higher or lower as the figure wants, and neither on a tie or a gap", () => {
    expect(better(0.6, 0.4, true)).toBe("a");
    expect(better(0.6, 0.4, false)).toBe("b");
    expect(better(1, 1, true)).toBeNull();
    expect(better(null, 1, true)).toBeNull();
  });

  it("takes the version the search put forward, and nothing when there is none", () => {
    expect(bestOf(null)).toBeNull();
    expect(bestOf(detail(0.5, 10, 9, "failed"))?.sharpe).toBe(0.5);
  });

  it("opens on two strategies that have a search, not on a draft that sorts first", () => {
    const searched = (id: string): Strategy => ({ ...strategy(id), latest_search: { artifact: `search-${id}.json` } as Strategy["latest_search"] });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([]))));

    render(<Compare strategies={[strategy("a_draft"), searched("beta"), searched("gamma")]} />);

    expect(screen.getByRole("combobox", { name: "Strategy A" })).toHaveValue("beta");
    expect(screen.getByRole("combobox", { name: "Strategy B" })).toHaveValue("gamma");
  });

  it("says one strategy cannot be compared", () => {
    render(<Compare strategies={[strategy("only")]} />);

    expect(screen.getByText("One strategy so far")).toBeInTheDocument();
  });

  it("sets two strategies' newest searches side by side", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        const id = path.split("/")[3];
        if (path.endsWith("/searches")) {
          return new Response(JSON.stringify([{ artifact: `search-${id}.json`, readable: true }]));
        }
        return new Response(
          JSON.stringify(id === "alpha" ? detail(0.64, 89.3, 19.4, "failed") : detail(1.2, 40.0, 9.0, "passed")),
        );
      }),
    );
    render(<Compare strategies={[strategy("alpha"), strategy("beta")]} />);

    const score = (await screen.findByText("Score (Sharpe) on old prices")).closest("tr") as HTMLElement;
    await waitFor(() => expect(within(score).getByText("1.20 ◀")).toBeInTheDocument());
    const fall = screen.getByText("Worst fall").closest("tr") as HTMLElement;
    expect(within(fall).getByText("−9.0% ◀")).toBeInTheDocument();
    const returns = screen.getByText("Total return").closest("tr") as HTMLElement;
    expect(within(returns).getByText("+89.3% ◀")).toBeInTheDocument();
    expect(screen.getByText("Beat 97 of 100")).toBeInTheDocument();
    expect(screen.getByText("Beat 25 of 100, needed 95")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox", { name: "Strategy B" }), { target: { value: "alpha" } });
    expect(await screen.findByText("Both sides are the same strategy.")).toBeInTheDocument();
  });
});
