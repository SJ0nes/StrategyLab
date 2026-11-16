import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { deployBlocker } from "../src/App";
import type { Check, Deployed as DeployedRecord, DeploymentStatus, Live, Strategy } from "../src/api";
import { DeployDialog } from "../src/components/DeployDialog";
import { Deployed, decisionWords } from "../src/pages/Deployed";
import { Trades } from "../src/pages/Trades";

const STRATEGY: Strategy = {
  id: "ma_crossover",
  name: "ma_crossover",
  description: "Trend following.",
  config: "configs/ma_crossover.yaml",
  config_hash: "a".repeat(64),
  state: "draft",
  latest_search: {
    artifact: "search-a.json",
    generated_at: "2026-11-10T18:40:30+00:00",
    cells: 90,
    candidate: { fast: 10, slow: 80, trend: 150 },
    sharpe: 0.64,
    max_drawdown_pct: 19.4,
    gate: { status: "failed", beaten: 25, required: 95, scoreable_trials: 100, summary: "x" },
    config_changed_since: false,
  },
  rules: { entry: ["Buy rule."], exit: ["Sell rule."], search: [], fixed: [], versions: 90 },
  source: null,
  confirmed: true,
  confirmed_at: "2026-11-09T10:00:00+00:00",
  stale: false,
};

const DEPLOYED: DeployedRecord = {
  available: true,
  strategy_id: "ma_crossover",
  artifact: "search-a.json",
  params: { fast: 10, slow: 80, trend: 150 },
  proven: false,
  deployed_at: "2026-11-02T12:00:00+00:00",
  paused: false,
  paused_at: null,
  current: true,
  problem: null,
};

const LIVE: Live = {
  available: true,
  checks: [
    { bar_date: "2026-11-02", recorded_at: "2026-11-02T17:45:00+00:00", equity: 10000, cash: 10000, currency: "GBP", position_quantity: 0, order_side: "buy", order_outcome: "sent", venue_order_id: "41", fill: { price: 101.5, quantity: 98.5, currency: "GBP", filled_at: "2026-11-03T08:00:03+00:00" } },
    { bar_date: "2026-11-03", recorded_at: "2026-11-03T17:45:00+00:00", equity: 10500, cash: 20, currency: "GBP", position_quantity: 98.5, order_side: null, order_outcome: "none", venue_order_id: null, fill: null },
    { bar_date: "2026-11-04", recorded_at: "2026-11-04T17:45:00+00:00", equity: 10200, cash: 20, currency: "GBP", position_quantity: 98.5, order_side: "sell", order_outcome: "failed", venue_order_id: null, fill: null },
  ],
  fills_readable: true,
  figures: {
    return_pct: 2,
    sharpe: null,
    max_drawdown_pct: 2.857,
    sent: 1,
    failed: 1,
    checks: 3,
    why_missing: { sharpe: "needs 20 checks; 3 so far" },
  },
  figures_are_the_accounts: false,
  decisions: [
    { bar_date: "2026-11-04", recorded_at: "2026-11-04T17:45:00+00:00", action: "exit_long", risk_verdict: "allow", risk_reason: "within limits", conditions_met: ["sma cross"], conditions_failed: [] },
    { bar_date: "2026-11-03", recorded_at: "2026-11-03T17:45:00+00:00", action: "enter_long", risk_verdict: "reject", risk_reason: "daily loss lock", conditions_met: ["a", "b"], conditions_failed: [] },
    { bar_date: "2026-11-02", recorded_at: "2026-11-02T17:45:00+00:00", action: "hold", risk_verdict: "allow", risk_reason: "", conditions_met: [], conditions_failed: ["a"] },
  ],
  loss_lock: false,
};

const STATUS: DeploymentStatus = {
  deployment: DEPLOYED,
  deployed: [{ strategy_id: "ma_crossover", paused: false, proven: false }],
  stop_all: { stopped: false, stopped_at: null, damaged: false },
  venue_unready: null,
  schedule: { next_check: "2026-11-12T17:30:00+00:00", why_not: null, missed: ["2026-11-05"], checked_today: false },
  live: LIVE,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("what may be deployed", () => {
  it("says why not, or nothing when it may", () => {
    expect(deployBlocker(null, "ma_crossover")).toContain("could not be read");
    expect(deployBlocker({ ...STATUS, deployment: null }, "ma_crossover")).toBeNull();
    expect(deployBlocker({ ...STATUS, deployment: null, venue_unready: "the practice account is not set" }, "x")).toBe(
      "the practice account is not set",
    );
    // Another strategy deployed is no bar: several share the account.
    expect(deployBlocker({ ...STATUS, deployment: null }, "other")).toBeNull();
    // An answer still naming the strategy shown before does not block this one.
    expect(deployBlocker(STATUS, "other")).toBeNull();
    expect(deployBlocker(STATUS, "ma_crossover")).toBe("it is already deployed");
    expect(deployBlocker({ ...STATUS, deployment: { available: false, reason: "damaged" } }, "x")).toContain("damaged");
  });

  it("words each decision", () => {
    expect(decisionWords("enter_long", "allow")).toBe("Buy");
    expect(decisionWords("enter_long", "reject")).toBe("Buy, blocked");
    expect(decisionWords("exit_long", "allow")).toBe("Sell");
    expect(decisionWords("hold", "reject")).toBe("No trade");
    expect(decisionWords(null, null)).toBe("Unknown");
  });
});

describe("the deployed strategy's Dashboard", () => {
  const onStart = vi.fn();
  const onChanged = vi.fn();

  afterEach(() => {
    onStart.mockReset();
    onChanged.mockReset();
  });

  it("shows the live figures the server worked out, and says which are missing and why", () => {
    render(<Deployed strategy={STRATEGY} deployed={DEPLOYED} status={STATUS} onStart={onStart} onChanged={onChanged} />);

    expect(screen.getByText("Deployed, unproven")).toBeInTheDocument();
    expect(screen.getByText(/tests the plumbing, not the strategy/)).toBeInTheDocument();
    expect(screen.getByText("+2.0%")).toBeInTheDocument();
    expect(screen.getByText("needs 20 checks; 3 so far")).toBeInTheDocument();
    const sharpeCard = screen.getByText("needs 20 checks; 3 so far").closest(".kpi") as HTMLElement;
    expect(within(sharpeCard).getByText("none")).toBeInTheDocument();
    expect(screen.getByText("−2.9%")).toBeInTheDocument();
    expect(screen.getByText("1 refused by the venue")).toBeInTheDocument();
    expect(screen.getByText("98.5000 units")).toBeInTheDocument();
    expect(screen.getByText("1 weekday")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Account value, 2 Nov 2026 to 4 Nov 2026/ })).toBeInTheDocument();
  });

  it("says a proven strategy is deployed, without the warning", () => {
    render(
      <Deployed strategy={STRATEGY} deployed={{ ...DEPLOYED, proven: true }} status={STATUS} onStart={onStart} onChanged={onChanged} />,
    );

    expect(screen.getByText("Deployed")).toBeInTheDocument();
    expect(screen.queryByText(/tests the plumbing/)).toBeNull();
  });

  it("starts a daily check with Check now, and offers none while paused or stopped", () => {
    const { rerender } = render(
      <Deployed strategy={STRATEGY} deployed={DEPLOYED} status={STATUS} onStart={onStart} onChanged={onChanged} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Check now" }));
    expect(onStart.mock.calls).toEqual([["daily_check"]]);

    rerender(<Deployed strategy={STRATEGY} deployed={{ ...DEPLOYED, paused: true }} status={STATUS} onStart={onStart} onChanged={onChanged} />);
    expect(screen.getByRole("button", { name: "Check now" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Resume strategy" })).toBeInTheDocument();

    rerender(
      <Deployed
        strategy={STRATEGY}
        deployed={DEPLOYED}
        status={{ ...STATUS, stop_all: { stopped: true, stopped_at: null, damaged: false } }}
        onStart={onStart}
        onChanged={onChanged}
      />,
    );
    expect(screen.getByRole("button", { name: "Check now" })).toBeDisabled();
    expect(screen.getByText("On: no checks run")).toBeInTheDocument();

    rerender(
      <Deployed strategy={STRATEGY} deployed={DEPLOYED} status={{ ...STATUS, venue_unready: "the practice account is not set" }} onStart={onStart} onChanged={onChanged} />,
    );
    expect(screen.getByRole("button", { name: "Check now" })).toBeDisabled();
  });

  it("says the check is stopped when the file changed since deploying", () => {
    const stale = { ...DEPLOYED, current: false, problem: "the deployed strategy's file has changed since it was deployed" };
    render(<Deployed strategy={STRATEGY} deployed={stale} status={STATUS} onStart={onStart} onChanged={onChanged} />);

    expect(screen.getByRole("alert")).toHaveTextContent("changed since it was deployed");
    expect(screen.getByRole("button", { name: "Check now" })).toBeDisabled();
  });

  it("pauses and undeploys through the server, and reads the project again", async () => {
    const asked: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string, init?: RequestInit) => {
        asked.push(`${init?.method ?? "GET"} ${path}`);
        return new Response("{}");
      }),
    );
    render(<Deployed strategy={STRATEGY} deployed={DEPLOYED} status={STATUS} onStart={onStart} onChanged={onChanged} />);

    fireEvent.click(screen.getByRole("button", { name: "Pause strategy" }));
    fireEvent.click(screen.getByRole("button", { name: "Undeploy" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(2));
    expect(asked).toEqual([
      "POST /api/strategies/ma_crossover/deployment/pause",
      "POST /api/strategies/ma_crossover/deployment/undeploy",
    ]);
  });

  it("says the figures are the whole account's when several strategies share it", () => {
    const { rerender } = render(
      <Deployed strategy={STRATEGY} deployed={DEPLOYED} status={STATUS} onStart={onStart} onChanged={onChanged} />,
    );
    expect(screen.queryByRole("note")).toBeNull();

    const shared = { ...STATUS, live: { ...LIVE, figures_are_the_accounts: true } };
    rerender(<Deployed strategy={STRATEGY} deployed={DEPLOYED} status={shared} onStart={onStart} onChanged={onChanged} />);

    expect(screen.getByRole("note")).toHaveTextContent("whole account's");
  });

  it("says why no check is due when the server gives no schedule", () => {
    render(
      <Deployed strategy={STRATEGY} deployed={DEPLOYED} status={{ ...STATUS, schedule: null }} onStart={onStart} onChanged={onChanged} />,
    );

    const row = screen.getByText("Next automatic check").parentElement as HTMLElement;
    expect(row).toHaveTextContent("none");
  });

  it("says how it decided recently, in words", () => {
    render(<Deployed strategy={STRATEGY} deployed={DEPLOYED} status={STATUS} onStart={onStart} onChanged={onChanged} />);

    const recent = screen.getByRole("heading", { name: "Recent decisions" }).closest(".card") as HTMLElement;
    expect(within(recent).getByText("Sell")).toBeInTheDocument();
    expect(within(recent).getByText("Buy, blocked")).toBeInTheDocument();
    expect(within(recent).getByText("daily loss lock")).toBeInTheDocument();
  });

  it("never mentions buying and holding", () => {
    const { container } = render(
      <Deployed strategy={STRATEGY} deployed={DEPLOYED} status={STATUS} onStart={onStart} onChanged={onChanged} />,
    );

    expect(container.textContent?.toLowerCase()).not.toMatch(/buy.and.hold|benchmark|win rate/);
  });
});

describe("the Trades page", () => {
  it("lists the orders sent and refused, newest first, and every decision", () => {
    render(<Trades strategy={STRATEGY} status={STATUS} />);

    const rows = screen.getAllByRole("row").slice(1) as HTMLTableRowElement[];
    expect(rows.map((row) => [0, 1, 2].map((cell) => row.cells[cell]?.textContent).join(""))).toEqual([
      "2026-11-04SellRefused",
      "2026-11-02BuySent",
    ]);
    expect(rows[0]?.cells[3]?.textContent).toBe("");
    expect(rows[1]?.cells[3]?.textContent).toMatch(/^98.5 at 101.50 GBP, .*08:00$/);
    expect(screen.getByText(/its price appears here after the next check/)).toBeInTheDocument();
    expect(screen.queryByText(/fills log could not be read/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Daily decisions" }));
    expect(screen.getAllByRole("row")).toHaveLength(4);
    expect(screen.getByText("Blocked: daily loss lock")).toBeInTheDocument();
    expect(screen.getByText("2 of 2")).toBeInTheDocument();
  });

  it("says an order not yet filled is waiting, and when the fills cannot be read", () => {
    const waiting: Check = { ...LIVE.checks[0]!, fill: null };
    const live = { ...LIVE, checks: [waiting], fills_readable: false };
    render(<Trades strategy={STRATEGY} status={{ ...STATUS, live }} />);

    const [row] = screen.getAllByRole("row").slice(1) as HTMLTableRowElement[];
    expect(row?.cells[3]?.textContent).toBe("Not yet");
    expect(screen.getByText(/fills log could not be read/)).toBeInTheDocument();
  });

  it("says a strategy that is not deployed has no trades", () => {
    render(<Trades strategy={{ ...STRATEGY, id: "other", name: "other" }} status={STATUS} />);

    expect(screen.getByText(/other is not deployed/)).toBeInTheDocument();
  });
});

describe("the deploy dialog", () => {
  it("deploys only once the strategy's name is typed, and sends what was typed", async () => {
    const asked: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string, init?: RequestInit) => {
        asked.push(`${init?.method ?? "GET"} ${path} ${String(init?.body ?? "")}`);
        return new Response(JSON.stringify(DEPLOYED));
      }),
    );
    const onDone = vi.fn();
    render(<DeployDialog strategy={STRATEGY} onDone={onDone} onClose={vi.fn()} />);
    const deploy = screen.getByRole("button", { name: "Deploy" });

    expect(deploy).toBeDisabled();
    expect(screen.getByText(/Unproven\./)).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Strategy name" }), { target: { value: "ma_cross" } });
    expect(deploy).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Strategy name" }), { target: { value: " ma_crossover " } });
    fireEvent.click(deploy);

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    // What was typed is sent as typed; the server decides whether it matches.
    expect(asked).toEqual(['POST /api/strategies/ma_crossover/deploy {"typed":" ma_crossover "}']);
  });

  it("shows the server's refusal", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "the practice account is not set" }), { status: 409 })),
    );
    render(<DeployDialog strategy={STRATEGY} onDone={vi.fn()} onClose={vi.fn()} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Strategy name" }), { target: { value: "ma_crossover" } });
    fireEvent.click(screen.getByRole("button", { name: "Deploy" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Refused: the practice account is not set.");
  });
});
