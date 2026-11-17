import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DeploymentStatus, OwnTrades, SearchDetail, Strategy } from "../src/api";
import { Compare, better, bestOf, liveSide } from "../src/pages/Compare";
import { whenLabel } from "../src/words";

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

function own(returnPct: number | null, closed: number): OwnTrades {
  return { available: true, return_pct: returnPct, closed, wins: 0, open: null, charges: [], why_missing: returnPct === null ? "no trade has closed yet" : null };
}

function deployedStatus(id: string, trades: OwnTrades): DeploymentStatus {
  return {
    deployment: { available: true, strategy_id: id, deployed_at: "2026-11-02T12:00:00+00:00" },
    deployed: [],
    stop_all: { stopped: false, stopped_at: null, damaged: false },
    venue_unready: null,
    schedule: null,
    live: { available: true, own: trades },
  } as unknown as DeploymentStatus;
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

  it("reads a side's own live trades, or says why there are none", () => {
    const status = deployedStatus("alpha", own(12.5, 2));
    const ready = (value: DeploymentStatus | null) => ({ state: "ready" as const, value });

    expect(liveSide({ state: "loading" }).why).toBe("reading…");
    expect(liveSide({ state: "failed", message: "no answer" }).why).toBe("no answer");
    expect(liveSide(ready(null)).why).toBe("not deployed");
    expect(liveSide(ready({ ...status, deployment: null })).why).toBe("not deployed");
    expect(liveSide(ready({ ...status, deployment: { available: false, reason: "damaged" } } as DeploymentStatus)).why).toBe("damaged");
    expect(liveSide(ready({ ...status, live: null }))).toEqual({ own: null, since: "2026-11-02T12:00:00+00:00", why: "no live figures" });
    expect(liveSide(ready({ ...status, live: { available: false, reason: "log unreadable" } })).why).toBe("log unreadable");
    expect(
      liveSide(ready({ ...status, live: { available: true, own: { available: false, reason: "fills unreadable" } } } as unknown as DeploymentStatus)).why,
    ).toBe("fills unreadable");
    expect(liveSide(ready(status))).toEqual({ own: own(12.5, 2), since: "2026-11-02T12:00:00+00:00", why: "" });
    expect(liveSide(ready(deployedStatus("alpha", own(null, 0)))).why).toBe("no trade has closed yet");
  });

  it("sets deployed strategies' own live returns side by side, and marks no side on a count of trades", async () => {
    const answers: Record<string, DeploymentStatus> = {
      alpha: deployedStatus("alpha", own(12.5, 2)),
      beta: deployedStatus("beta", own(-3, 5)),
      gamma: { ...deployedStatus("gamma", own(null, 0)), deployment: null },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        const asked = /strategy_id=([a-z]+)/.exec(path);
        return new Response(JSON.stringify(asked ? answers[asked[1]!] : []));
      }),
    );
    render(<Compare strategies={[strategy("alpha"), strategy("beta"), strategy("gamma")]} />);

    const live = (await screen.findByText("Own return, live")).closest("tr") as HTMLElement;
    await waitFor(() => expect(within(live).getByText("+12.5% ◀")).toBeInTheDocument());
    expect(within(live).getByText("−3.0%")).toBeInTheDocument();
    const trades = screen.getByText("Closed trades, live").closest("tr") as HTMLElement;
    expect(trades).toHaveTextContent("25");
    expect(trades).not.toHaveTextContent("◀");

    fireEvent.change(screen.getByRole("combobox", { name: "Strategy B" }), { target: { value: "gamma" } });
    const deployed = screen.getByText("Deployed").closest("tr") as HTMLElement;
    await waitFor(() => expect(deployed).toHaveTextContent("not deployed"));
    const [, sideA] = within(deployed).getAllByRole("cell");
    expect(sideA).toHaveTextContent(whenLabel("2026-11-02T12:00:00+00:00"));
    const again = screen.getByText("Own return, live").closest("tr") as HTMLElement;
    expect(within(again).getByText("+12.5%")).toBeInTheDocument();
    expect(again).not.toHaveTextContent("◀");
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
