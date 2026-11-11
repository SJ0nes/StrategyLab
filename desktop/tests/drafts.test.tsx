import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LatestSearch, Source, Strategy } from "../src/api";
import { Dashboard, draftSteps } from "../src/pages/Dashboard";

const GATE = { status: "failed" as const, beaten: 25, required: 95, scoreable_trials: 100, summary: "sample" };

const SEARCH: LatestSearch = {
  artifact: "search-b.json",
  generated_at: "2026-11-10T18:40:30+00:00",
  cells: 3,
  candidate: { short: 20 },
  sharpe: 0.58,
  max_drawdown_pct: 12.5,
  gate: { ...GATE, status: "not_computed", beaten: null, required: null },
  config_changed_since: false,
};

const SOURCE: Source = {
  available: true,
  url: "https://www.youtube.com/watch?v=sample",
  title: "A simple pullback strategy",
  channel: "Sample Channel",
  watched_at: "2026-11-12",
  rules: [{ rule: "Buy when price closes back above the 20-day average.", at: "3:45" }],
  left_out: [{ what: "Stop-loss 2% below the buy price", why: "Stop-losses are not supported yet.", at: "5:02" }],
};

const DRAFT: Strategy = {
  id: "trend_pullback",
  name: "trend_pullback",
  description: "Buy pullbacks in an uptrend.",
  config: "configs/trend_pullback.yaml",
  config_hash: "b".repeat(64),
  state: "draft",
  latest_search: null,
  rules: {
    entry: ["The 50-day average price is above the 200-day average price.", "The price crosses above the average price over short days."],
    exit: ["The price crosses below the 50-day average price."],
    search: ["short: 10 to 30 days, in steps of 10 (3 values)."],
    fixed: ["Each trade uses 50% of the account."],
    versions: 3,
  },
  source: SOURCE,
  confirmed: false,
  confirmed_at: null,
  stale: false,
};

const states = (strategy: Strategy) => draftSteps(strategy).map((step) => step.state);
const actions = (strategy: Strategy) => draftSteps(strategy).map((step) => step.action);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the path to deployment", () => {
  it("starts at confirming the rules, with nothing else on offer", () => {
    expect(states(DRAFT)).toEqual(["done", "here", "waiting", "waiting", "waiting"]);
    expect(actions(DRAFT)).toEqual([null, "confirm", null, null, null]);
    expect(draftSteps(DRAFT)[0]?.title).toBe("Read from a video");
  });

  it("offers a backtest once the rules are confirmed", () => {
    const confirmed = { ...DRAFT, confirmed: true, confirmed_at: "2026-11-12T10:00:00+00:00" };

    expect(states(confirmed)).toEqual(["done", "done", "here", "waiting", "waiting"]);
    expect(actions(confirmed)).toEqual([null, null, "search", null, null]);
  });

  it("offers the luck test once a search has run on the file as it is now", () => {
    const tested = { ...DRAFT, confirmed: true, latest_search: SEARCH };

    expect(states(tested)).toEqual(["done", "done", "done", "here", "waiting"]);
    expect(actions(tested)).toEqual([null, null, null, "luck_test", null]);
    expect(draftSteps(tested)[2]?.note).toBe("Best of 3 versions: short 20, score 0.58.");
  });

  it("does not count a search that ran on an earlier version of the file", () => {
    const old = { ...DRAFT, confirmed: true, latest_search: { ...SEARCH, config_changed_since: true } };

    expect(states(old)).toEqual(["done", "done", "here", "waiting", "waiting"]);
    expect(draftSteps(old)[2]?.note).toContain("earlier version of the file");
  });

  it("marks a failed luck test as failed and offers no second try", () => {
    const failed = { ...DRAFT, confirmed: true, latest_search: { ...SEARCH, gate: GATE } };

    expect(states(failed)).toEqual(["done", "done", "done", "failed", "waiting"]);
    expect(actions(failed)).toEqual([null, null, null, null, null]);
    expect(draftSteps(failed)[3]?.note).toContain("Beat 25 of 100, needed 95.");
    expect(draftSteps(failed)[3]?.note).toContain("same answer");
  });

  it("offers a luck test again when the last one ran too few trials to decide", () => {
    const few = { ...DRAFT, confirmed: true, latest_search: { ...SEARCH, gate: { ...GATE, status: "undetermined" as const } } };

    expect(actions(few)[3]).toBe("luck_test");
  });

  it("offers deploying once a backtest of the confirmed rules has run, and nothing blocks it", () => {
    const passed = {
      ...DRAFT,
      confirmed: true,
      latest_search: { ...SEARCH, gate: { ...GATE, status: "passed" as const, beaten: 97 } },
    };

    expect(states(passed)).toEqual(["done", "done", "done", "done", "waiting"]);
    expect(actions(passed)).toEqual([null, null, null, null, null]);
    expect(draftSteps(passed, null).map((step) => step.action)).toEqual([null, null, null, null, "deploy"]);
    expect(draftSteps(passed, null)[4]?.note).toBe("It passed the luck test.");
  });

  it("offers deploying an unproven strategy, and says it is unproven", () => {
    const failed = { ...DRAFT, confirmed: true, latest_search: { ...SEARCH, gate: GATE } };

    const steps = draftSteps(failed, null);

    expect(steps[4]?.action).toBe("deploy");
    expect(steps[4]?.note).toContain("Unproven");
  });

  it("says what blocks deploying, and offers nothing", () => {
    const tested = { ...DRAFT, confirmed: true, latest_search: SEARCH };

    const steps = draftSteps(tested, "the practice account is not set: T212_API_KEY is missing");

    expect(steps[4]?.action).toBeNull();
    expect(steps[4]?.note).toBe("The practice account is not set: T212_API_KEY is missing.");
  });

  it("offers no deploying before the rules are confirmed and backtested, whatever else holds", () => {
    expect(draftSteps(DRAFT, null)[4]?.action).toBeNull();
    expect(draftSteps({ ...DRAFT, confirmed: true }, null)[4]?.action).toBeNull();
  });

  it("asks for the rules again when the file changed since they were confirmed", () => {
    const stale = { ...DRAFT, stale: true };

    expect(draftSteps(stale)[1]?.note).toContain("changed since you last confirmed");
    expect(actions(stale)[1]).toBe("confirm");
  });

  it("says a strategy written by hand was written, not read from a video", () => {
    const hand = { ...DRAFT, source: null };

    expect(draftSteps(hand)[0]?.title).toBe("Written as a strategy file");
    expect(draftSteps(hand)[0]?.note).toBe("configs/trend_pullback.yaml.");
  });
});

describe("the draft page", () => {
  const onStart = vi.fn();
  const onChanged = vi.fn();

  afterEach(() => {
    onStart.mockReset();
    onChanged.mockReset();
  });

  it("shows the rules, what the video said that was left out, and where it came from", () => {
    render(<Dashboard strategy={DRAFT} onStart={onStart} onChanged={onChanged} />);

    expect(screen.getByText("What the video says, as rules")).toBeInTheDocument();
    expect(screen.getByText("The 50-day average price is above the 200-day average price.")).toBeInTheDocument();
    expect(screen.getByText("The price crosses below the 50-day average price.")).toBeInTheDocument();
    expect(screen.getByText("Settings to try: 3 versions")).toBeInTheDocument();
    expect(screen.getByText("From the video, but left out")).toBeInTheDocument();
    expect(screen.getByText("Stop-loss 2% below the buy price.")).toBeInTheDocument();
    expect(screen.getByText(/Stop-losses are not supported yet\. \(at 5:02\)/)).toBeInTheDocument();
    expect(screen.getByText(/"A simple pullback strategy" by Sample Channel/)).toBeInTheDocument();
  });

  it("shows the video's link as text, never as a link that could be followed", () => {
    const { container } = render(<Dashboard strategy={DRAFT} onStart={onStart} onChanged={onChanged} />);

    expect(screen.getByText(/youtube\.com\/watch\?v=sample/)).toBeInTheDocument();
    expect(container.querySelector("a")).toBeNull();
  });

  it("shows no left-out box for a strategy written by hand", () => {
    render(<Dashboard strategy={{ ...DRAFT, source: null }} onStart={onStart} onChanged={onChanged} />);

    expect(screen.getByText("Its rules")).toBeInTheDocument();
    expect(screen.queryByText("From the video, but left out")).toBeNull();
  });

  it("says so when the record of where it came from cannot be read", () => {
    const broken = { ...DRAFT, source: { available: false as const, reason: "Damaged, says the server." } };
    render(<Dashboard strategy={broken} onStart={onStart} onChanged={onChanged} />);

    expect(screen.getByRole("alert")).toHaveTextContent("Damaged, says the server.");
  });

  it("records a confirmation when Rules are right is pressed, then reads the project again", async () => {
    const asked: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string, init?: RequestInit) => {
        asked.push(`${init?.method ?? "GET"} ${path} ${String(init?.body ?? "")}`);
        return new Response(JSON.stringify({ confirmed: true }));
      }),
    );
    render(<Dashboard strategy={DRAFT} onStart={onStart} onChanged={onChanged} />);

    fireEvent.click(screen.getByRole("button", { name: "Rules are right" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(asked).toEqual([`POST /api/strategies/trend_pullback/confirm {"config_hash":"${"b".repeat(64)}"}`]);
    expect(onStart).not.toHaveBeenCalled();
  });

  it("shows the server's words when a confirmation is refused", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "the confirmation could not be recorded" }), { status: 500 })),
    );
    render(<Dashboard strategy={DRAFT} onStart={onStart} onChanged={onChanged} />);

    fireEvent.click(screen.getByRole("button", { name: "Rules are right" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Refused: the confirmation could not be recorded.");
    // Read again, so rules that changed under the page are the ones on it.
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("asks for a backtest, then a luck test, through the job dialog", () => {
    const confirmed = { ...DRAFT, confirmed: true };
    const { rerender } = render(<Dashboard strategy={confirmed} onStart={onStart} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole("button", { name: "Run backtest" }));

    rerender(<Dashboard strategy={{ ...confirmed, latest_search: SEARCH }} onStart={onStart} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole("button", { name: "Run luck test" }));

    expect(onStart.mock.calls).toEqual([["search"], ["luck_test"]]);
  });

  it("opens the deploy dialog from the last step when it may", () => {
    const onDeploy = vi.fn();
    const tested = { ...DRAFT, confirmed: true, latest_search: SEARCH };
    render(<Dashboard strategy={tested} onStart={onStart} onChanged={onChanged} blocker={null} onDeploy={onDeploy} />);

    fireEvent.click(screen.getByRole("button", { name: "Deploy…" }));

    expect(onDeploy).toHaveBeenCalledTimes(1);
    expect(onStart).not.toHaveBeenCalled();
  });

  it("keeps Deploy disabled when something blocks it", () => {
    const passed = {
      ...DRAFT,
      confirmed: true,
      latest_search: { ...SEARCH, gate: { ...GATE, status: "passed" as const, beaten: 97 } },
    };
    render(<Dashboard strategy={passed} onStart={onStart} onChanged={onChanged} />);

    expect(screen.getByRole("button", { name: "Deploy…" })).toBeDisabled();
    const steps = screen.getByRole("heading", { name: "Path to deployment" }).parentElement as HTMLElement;
    expect(within(steps).getAllByRole("button")).toHaveLength(1);
  });

  it("never mentions buying and holding", () => {
    const { container } = render(<Dashboard strategy={DRAFT} onStart={onStart} onChanged={onChanged} />);

    expect(container.textContent?.toLowerCase()).not.toMatch(/buy.and.hold|benchmark/);
  });
});
