import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Cell, Exam, Graveyard, Overview, SearchDetail, SearchEntry, Strategy } from "../src/api";
import { drawable, gridStep } from "../src/components/Chart";
import { Backtest } from "../src/pages/Backtest";
import { Improve, ramp } from "../src/pages/Improve";
import { Tests, examNote, examRow, searchRow } from "../src/pages/Tests";
import { REASON_LABEL, dayLabel, gatePill, gateSentence, reasonDetail, signedPct, versionLabel, whenLabel } from "../src/words";

const GATE = { status: "failed" as const, beaten: 25, required: 95, scoreable_trials: 100, summary: "Gate summary." };

const SUMMARY = {
  artifact: "search-b.json",
  generated_at: "2026-11-10T18:40:30.666974+00:00",
  candidate: { fast: 10, slow: 40, trend: 150 },
  sharpe: 0.64,
  max_drawdown_pct: 19.4,
  gate: GATE,
  config_changed_since: false,
};

function cell(fast: number, slow: number, trend: number, sharpe: number | null, extra: Partial<Cell> = {}): Cell {
  return {
    params: { fast, slow, trend },
    sharpe,
    max_drawdown_pct: sharpe === null ? null : 12.5,
    trade_count: fast + slow,
    plateau_score: 0.5,
    neighbour_count: 3,
    total_return_pct: sharpe === null ? null : 25,
    mark: null,
    curve: sharpe === null ? null : [null, 10000, 12500],
    ...extra,
  };
}

/** A two-by-two grid at each of two trend values: eight versions. */
const DETAIL: SearchDetail = {
  ...SUMMARY,
  available: true,
  instrument: "SPY",
  window: { start: "2005-01-01", end: "2018-12-31", bars: 3523 },
  starting_equity: 10000,
  max_drawdown_ceiling_pct: 25,
  parameters: ["fast", "slow", "trend"],
  axes: { fast: [5, 10], slow: [20, 40], trend: [100, 150] },
  dates: ["2005-01-03", "2010-01-04", "2018-12-31"],
  cells: [
    cell(5, 20, 100, 0.11),
    cell(10, 20, 100, 0.22),
    cell(5, 40, 100, 0.33),
    cell(10, 40, 100, 0.44),
    cell(5, 20, 150, 0.55, { mark: "edge_winner", max_drawdown_pct: 31.2 }),
    cell(10, 20, 150, null),
    cell(5, 40, 150, -0.2, { total_return_pct: -10 }),
    cell(10, 40, 150, 0.64, { mark: "candidate", total_return_pct: 89.3, max_drawdown_pct: 19.4 }),
  ],
  rejections: [],
  ceiling: null,
};

const UNREAD: SearchEntry = {
  artifact: "search-a.json",
  readable: false,
  generated_at: "2026-08-22T21:32:27+00:00",
  reason: "written in another format (version 4); this app reads version 8",
};

const LIST: SearchEntry[] = [{ ...SUMMARY, cells: 8, readable: true }, UNREAD];

const GRAVEYARD: Graveyard = {
  available: true,
  records: 37,
  unread_lines: 0,
  entries: [
    {
      candidate: { fast: 10, slow: 80, trend: 150 },
      reason: "below_noise_ceiling",
      times: 1,
      last_recorded_at: "2026-11-11T02:11:12+00:00",
      sharpe: 0.64,
      max_drawdown_pct: 19.4,
    },
    {
      candidate: { fast: 5, slow: 80, trend: 100 },
      reason: "edge_winner",
      times: 9,
      last_recorded_at: "2026-11-11T02:11:12+00:00",
      sharpe: 0.6,
      max_drawdown_pct: 23.2,
    },
  ],
};

const STRATEGY: Strategy = {
  id: "ma_crossover",
  name: "ma_crossover",
  description: "Trend following.",
  config: "configs/ma_crossover.yaml",
  config_hash: "a".repeat(64),
  state: "draft",
  latest_search: { ...SUMMARY, cells: 8 },
  rules: { entry: ["Buy rule."], exit: ["Sell rule."], search: ["fast: 5 to 30 days."], fixed: [], versions: 90 },
  source: null,
  confirmed: false,
  confirmed_at: null,
  stale: false,
};

const OVERVIEW: Overview = {
  protocol_version: 1,
  held_back_reads: 0,
  prices: { available: false, reason: "sample" },
  kill_switch: false,
  venue: { environment: null, key_present: false },
  strategies: [STRATEGY],
  unreadable_configs: [],
  tour_seen: true,
};

/** Answer each request from a table of path to body; an unlisted path is a 404. */
function serve(routes: Record<string, unknown>): string[] {
  const asked: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      asked.push(path);
      return path in routes
        ? new Response(JSON.stringify(routes[path]))
        : new Response(JSON.stringify({ detail: "not found" }), { status: 404 });
    }),
  );
  return asked;
}

const ROUTES = {
  "/api/strategies/ma_crossover/searches": LIST,
  "/api/strategies/ma_crossover/searches/search-b.json": DETAIL,
  "/api/strategies/ma_crossover/exams": [],
  "/api/graveyard": GRAVEYARD,
};

const onStart = vi.fn();

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  onStart.mockReset();
});

describe("wording", () => {
  it("writes dates and instants the way the window shows them", () => {
    expect(dayLabel("2018-12-31")).toBe("31 Dec 2018");
    expect(dayLabel("2005-01-03")).toBe("3 Jan 2005");
    expect(dayLabel("not a date")).toBe("not a date");
    expect(whenLabel("2026-11-10T18:40:30.666974+00:00")).toBe("10 Nov 2026, 18:40");
    expect(whenLabel(null)).toBe("date not recorded");
  });

  it("writes a version, a signed percentage and each luck test outcome", () => {
    expect(versionLabel({ fast: 10, slow: 80 })).toBe("fast 10, slow 80");
    expect(versionLabel(null)).toBe("none");
    expect(signedPct(89.25)).toBe("+89.3%");
    expect(signedPct(-10)).toBe("−10.0%");
    expect(gatePill(GATE)).toEqual({ tone: "bad", text: "Beat 25 of 100, needed 95" });
    expect(gatePill({ ...GATE, status: "passed", beaten: 97 })).toEqual({ tone: "good", text: "Beat 97 of 100" });
    expect(gatePill({ ...GATE, status: "not_computed" }).text).toBe("No luck test");
    expect(gateSentence({ ...GATE, status: "passed", beaten: 97 })).toContain("beat 97 of 100");
    expect(gateSentence({ ...GATE, status: "passed", beaten: 97 })).toContain("more than luck");
    expect(gateSentence(GATE)).toContain("cannot go on to the final exam");
  });

  it("has words for every reason the server can give", () => {
    const reasons = ["edge_winner", "degenerate_plateau", "below_noise_ceiling", "drawdown_ceiling", "backtest_failed"] as const;
    const labels = reasons.map((reason) => REASON_LABEL[reason]);
    const details = reasons.map((reason) =>
      reasonDetail({ candidate: {}, reason, times: 1, last_recorded_at: "", sharpe: 0.5, max_drawdown_pct: 31.2 }),
    );

    expect(new Set(labels).size).toBe(5);
    expect(new Set(details).size).toBe(5);
    expect(details[3]).toContain("31.2%");
    expect(details[0]).toContain("0.50");
  });
});

describe("the chart", () => {
  it("draws only the points that have a value, each with its own date", () => {
    expect(drawable(["a", "b", "c", "d"], [null, 1, null, 2])).toEqual([
      { date: "b", value: 1 },
      { date: "d", value: 2 },
    ]);
  });

  it("spaces gridlines at round amounts", () => {
    expect(gridStep(9000)).toBe(2500);
    expect(gridStep(400)).toBe(100);
    expect(gridStep(3)).toBe(1);
  });

  it("gives the lowest score the dark end of the scale and the highest the bright end", () => {
    expect(ramp(0)).toBe("rgb(68,1,84)");
    expect(ramp(1)).toBe("rgb(253,231,37)");
    expect(ramp(7)).toBe("rgb(253,231,37)");
  });
});

describe("Improve", () => {
  it("draws the slice of the grid that holds the best version, and marks it", async () => {
    serve(ROUTES);
    render(<Improve strategy={STRATEGY} onStart={onStart} />);

    const best = await screen.findByRole("button", { name: "fast 10, slow 40, trend 150, score 0.64" });
    expect(best).toHaveClass("best", "sel");
    expect(screen.getByRole("button", { name: "fast 5, slow 40, trend 150, score -0.20" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "fast 10, slow 20, trend 150, no score" })).toHaveTextContent("–");
    // The other trend value's versions are not on screen until it is chosen.
    expect(screen.queryByRole("button", { name: /trend 100, score/ })).toBeNull();
    expect(screen.getByText("Best found")).toBeInTheDocument();
    expect(screen.getByText("Beat 25 of 100, needed 95")).toBeInTheDocument();
    expect(screen.getByText("+89.3%")).toBeInTheDocument();
  });

  it("shows another slice when its value is pressed", async () => {
    serve(ROUTES);
    render(<Improve strategy={STRATEGY} onStart={onStart} />);
    await screen.findByText("Best found");

    fireEvent.click(within(screen.getByRole("group", { name: "trend" })).getByRole("button", { name: "100" }));

    expect(screen.getByRole("button", { name: "fast 5, slow 20, trend 100, score 0.11" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /trend 150, score/ })).toBeNull();
  });

  it("shows the pressed version's own figures, and says the luck test was not run on it", async () => {
    serve(ROUTES);
    render(<Improve strategy={STRATEGY} onStart={onStart} />);

    fireEvent.click(await screen.findByRole("button", { name: "fast 5, slow 20, trend 150, score 0.55" }));

    expect(screen.getByRole("heading", { name: "fast 5, slow 20, trend 150" })).toBeInTheDocument();
    expect(screen.getByText("On the edge")).toBeInTheDocument();
    expect(screen.getByText("Not run on this version")).toBeInTheDocument();
    expect(screen.queryByText("Beat 25 of 100, needed 95")).toBeNull();
    expect(screen.getByText("−31.2%")).toBeInTheDocument();
    expect(screen.getByText(/above the 25% limit/)).toBeInTheDocument();
  });

  it("says a version whose replay failed has no score, not a zero", async () => {
    serve(ROUTES);
    render(<Improve strategy={STRATEGY} onStart={onStart} />);

    fireEvent.click(await screen.findByRole("button", { name: "fast 10, slow 20, trend 150, no score" }));

    expect(screen.getByText(/stopped early/)).toBeInTheDocument();
    expect(screen.getAllByText("none").length).toBe(3);
  });

  it("asks for a search or a luck test when its buttons are pressed, and starts neither itself", async () => {
    const asked = serve(ROUTES);
    render(<Improve strategy={STRATEGY} onStart={onStart} />);
    await screen.findByText("Best found");
    const before = asked.length;

    fireEvent.click(screen.getByRole("button", { name: "Search again" }));
    fireEvent.click(screen.getByRole("button", { name: "Run the luck test" }));

    expect(onStart.mock.calls).toEqual([["search"], ["luck_test"]]);
    expect(asked.length).toBe(before);
  });

  it("cannot deploy anything", async () => {
    serve(ROUTES);
    render(<Improve strategy={STRATEGY} onStart={onStart} />);
    await screen.findByText("Best found");

    expect(screen.getByRole("button", { name: "Deploy this version…" })).toBeDisabled();
  });

  it("lists what was ruled out across the project, once each, in plain words", async () => {
    serve(ROUTES);
    render(<Improve strategy={STRATEGY} onStart={onStart} />);
    fireEvent.click(await screen.findByRole("tab", { name: "Ruled out" }));

    expect(await screen.findByText("Failed the luck test")).toBeInTheDocument();
    expect(screen.getByText("On the edge")).toBeInTheDocument();
    expect(screen.getByText(/37 rejections are on record, 2 of them different/)).toBeInTheDocument();
    expect(screen.getByText(/covers the whole project/)).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "9" })).toBeInTheDocument();
  });

  it("lists past searches, including one it could not read", async () => {
    serve(ROUTES);
    render(<Improve strategy={STRATEGY} onStart={onStart} />);
    fireEvent.click(await screen.findByRole("tab", { name: "Past searches" }));

    expect(screen.getByText("10 Nov 2026, 18:40")).toBeInTheDocument();
    expect(screen.getByText("22 Aug 2026, 21:32")).toBeInTheDocument();
    expect(screen.getByText(/Not read: written in another format \(version 4\)/)).toBeInTheDocument();
  });

  it("says so when no search has been run", async () => {
    serve({ ...ROUTES, "/api/strategies/ma_crossover/searches": [] });
    render(<Improve strategy={STRATEGY} onStart={onStart} />);

    expect(await screen.findByText("No search to show")).toBeInTheDocument();
  });

  it("says so when the only searches on record could not be read", async () => {
    const asked = serve({ ...ROUTES, "/api/strategies/ma_crossover/searches": [UNREAD] });
    render(<Improve strategy={STRATEGY} onStart={onStart} />);

    expect(await screen.findByText(/none could be read/)).toBeInTheDocument();
    expect(asked).toEqual(["/api/strategies/ma_crossover/searches"]);
  });

  it("shows the server's reason when the newest search is damaged", async () => {
    serve({
      ...ROUTES,
      "/api/strategies/ma_crossover/searches/search-b.json": { available: false, reason: "Damaged, says the server." },
    });
    render(<Improve strategy={STRATEGY} onStart={onStart} />);

    expect(await screen.findByText("Damaged, says the server.")).toBeInTheDocument();
  });

  it("reports a refused request and draws no grid", async () => {
    serve({});
    render(<Improve strategy={STRATEGY} onStart={onStart} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("refused");
    expect(screen.queryByRole("tab")).toBeNull();
  });

  it("never mentions buying and holding", async () => {
    serve(ROUTES);
    const { container } = render(<Improve strategy={STRATEGY} onStart={onStart} />);
    await screen.findByText("Best found");

    expect(container.textContent?.toLowerCase()).not.toMatch(/buy.and.hold|benchmark/);
  });
});

describe("Backtest", () => {
  it("shows the figures recorded for the version the search put forward", async () => {
    serve(ROUTES);
    render(<Backtest strategy={STRATEGY} onStart={onStart} />);

    expect(await screen.findByText("+89.3%")).toBeInTheDocument();
    expect(screen.getByText("0.64")).toBeInTheDocument();
    expect(screen.getByText("−19.4%")).toBeInTheDocument();
    expect(screen.getByText("50")).toBeInTheDocument();
    expect(screen.getByText("fast 10, slow 40, trend 150")).toBeInTheDocument();
    expect(screen.getByText(/the best of 8 versions tried/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Growth of 10,000 on SPY, 1 Jan 2005 to 31 Dec 2018/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "This strategy, 4 Jan 2010 to 31 Dec 2018" })).toBeInTheDocument();
  });

  it("asks for a search when Run backtest is pressed", async () => {
    serve(ROUTES);
    render(<Backtest strategy={STRATEGY} onStart={onStart} />);
    await screen.findByText("+89.3%");

    fireEvent.click(screen.getByRole("button", { name: "Run backtest" }));

    expect(onStart.mock.calls).toEqual([["search"]]);
  });

  it("shows no figure when the search put no version forward", async () => {
    const none = { ...DETAIL, candidate: null, cells: DETAIL.cells.map((each) => ({ ...each, mark: null })) };
    serve({ ...ROUTES, "/api/strategies/ma_crossover/searches/search-b.json": none });
    render(<Backtest strategy={STRATEGY} onStart={onStart} />);

    expect(await screen.findByText("The last search put no version forward")).toBeInTheDocument();
    expect(screen.queryByText("Total return")).toBeNull();
  });

  it("shows no figure when there is no search", async () => {
    serve({ ...ROUTES, "/api/strategies/ma_crossover/searches": [] });
    render(<Backtest strategy={STRATEGY} onStart={onStart} />);

    expect(await screen.findByText("No replay on record")).toBeInTheDocument();
    expect(screen.queryByText("Total return")).toBeNull();
  });
});

describe("Tests", () => {
  const EXAM: Exam = {
    artifact: "validation-a.json",
    readable: true,
    generated_at: "2026-12-01T09:00:00+00:00",
    params: { fast: 10, slow: 40 },
    outcome: "fail",
    reason: "Pooled Sharpe below the bar.",
    sharpe: 0.75,
    max_drawdown_pct: 8.5,
    trades: 31,
    folds: 6,
    read_number: 1,
  };

  it("words a search that ran a luck test, one that did not, and one it could not read", () => {
    const ran = searchRow(LIST[0] as SearchEntry);
    const plain = searchRow({ ...SUMMARY, cells: 8, readable: true, gate: { ...GATE, status: "not_computed" } });
    const unread = searchRow(UNREAD);

    expect(ran.what).toBe("Tried 8 versions on old prices, then compared the best against 100 scrambled histories.");
    expect(ran.result).toBe("Beat 25 of 100, needed 95");
    expect(ran.detail).toContain("fast 10, slow 40, trend 150, score 0.64. It beat 25 of 100 scrambled price histories and needed 95.");
    expect(ran.detail).not.toContain("Gate summary.");
    expect(plain.detail).toContain("ran no luck test");
    expect(plain.what).toBe("Tried 8 versions on old prices.");
    expect(plain.test).toBe("Search");
    expect(unread.result).toBe("Not read");
    expect(unread.detail).toContain("version 4");
  });

  it("words each outcome of a final exam", () => {
    expect(examRow(EXAM).result).toBe("Failed");
    expect(examRow(EXAM).tone).toBe("bad");
    expect(examRow(EXAM).what).toBe("One look at unseen years (look number 1).");
    expect(examRow(EXAM).detail).toContain("score 0.75, worst fall −8.5%, 31 trades, 6 periods. Pooled Sharpe below the bar.");
    expect(examRow({ ...EXAM, outcome: "pass" }).result).toBe("Passed");
    expect(examRow({ ...EXAM, outcome: "insufficient_data" }).tone).toBe("warn");
  });

  it("says why the final exam cannot be taken, by whether the luck test passed", () => {
    const passed = { ...STRATEGY, latest_search: { ...SUMMARY, cells: 8, gate: { ...GATE, status: "passed" as const } } };

    expect(examNote(STRATEGY)).toMatch(/locked: this strategy has not passed the luck test/);
    expect(examNote({ ...STRATEGY, latest_search: null })).toMatch(/locked/);
    expect(examNote(passed)).toMatch(/may take the final exam/);
    expect(examNote(passed, "this is a git worktree")).toBe("Final exam is locked: this is a git worktree.");
  });

  it("lists every test newest first, with the read counter from the protocol", async () => {
    serve({ ...ROUTES, "/api/strategies/ma_crossover/exams": [EXAM] });
    render(<Tests overview={{ ...OVERVIEW, held_back_reads: 1 }} strategy={STRATEGY} />);

    await screen.findByText("Failed");
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getAllByRole("cell")[1]?.textContent)).toEqual([
      "1 Dec 2026, 09:00",
      "10 Nov 2026, 18:40",
      "22 Aug 2026, 21:32",
    ]);
    const counters = screen.getAllByText(/^\d+$/).map((node) => node.textContent);
    expect(counters).toEqual(["1", "3"]);
    expect(screen.getByText("not in the app yet; strategylab diff checks them")).toBeInTheDocument();
  });

  it("opens a row's detail when it is pressed, and closes it again", async () => {
    serve(ROUTES);
    render(<Tests overview={OVERVIEW} strategy={STRATEGY} />);
    const row = (await screen.findByText("Beat 25 of 100, needed 95")).closest("tr") as HTMLElement;

    fireEvent.click(row);
    expect(screen.getByText(/It beat 25 of 100 scrambled price histories/)).toBeInTheDocument();
    fireEvent.click(row);
    await waitFor(() => expect(screen.queryByText(/It beat 25 of 100 scrambled price histories/)).toBeNull());
  });

  it("cannot take the final exam when its status cannot be read", async () => {
    serve(ROUTES);
    render(<Tests overview={OVERVIEW} strategy={{ ...STRATEGY, latest_search: { ...SUMMARY, cells: 8, gate: { ...GATE, status: "passed" as const } } }} />);
    await screen.findByText(/could not be read/);

    expect(screen.getByRole("button", { name: "Take final exam…" })).toBeDisabled();
  });

  it("cannot take the final exam while the server says it is locked", async () => {
    serve({ ...ROUTES, "/api/strategies/ma_crossover/final-exam": { refusal: "its newest search did not pass", artifact: null } });
    render(<Tests overview={OVERVIEW} strategy={STRATEGY} />);
    await screen.findByText("Beat 25 of 100, needed 95");

    expect(screen.getByRole("button", { name: "Take final exam…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Check live vs backtest" })).toBeDisabled();
  });

  it("says so when nothing has been run", async () => {
    serve({ ...ROUTES, "/api/strategies/ma_crossover/searches": [] });
    render(<Tests overview={OVERVIEW} strategy={STRATEGY} />);

    expect(await screen.findByText("No tests yet")).toBeInTheDocument();
  });
});

describe("the final exam", () => {
  const PASSED = {
    ...STRATEGY,
    latest_search: { ...SUMMARY, cells: 8, gate: { ...GATE, status: "passed" as const, beaten: 97 } },
  };

  it("opens with the looks used so far, and starts only once the phrase is typed", async () => {
    const asked: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string, init?: RequestInit) => {
        asked.push(`${init?.method ?? "GET"} ${path} ${String(init?.body ?? "")}`);
        if (path.endsWith("/final-exam") && init?.method !== "POST") {
          return new Response(JSON.stringify({ refusal: null, artifact: "search-b.json" }));
        }
        if (path.endsWith("/final-exam")) {
          return new Response(JSON.stringify({ id: "x", kind: "final_exam" }), { status: 201 });
        }
        const routes: Record<string, unknown> = ROUTES;
        return new Response(JSON.stringify(routes[path] ?? []));
      }),
    );
    const onExamStarted = vi.fn();
    render(<Tests overview={{ ...OVERVIEW, held_back_reads: 2 }} strategy={PASSED} onExamStarted={onExamStarted} />);

    const open = await screen.findByRole("button", { name: "Take final exam…" });
    await waitFor(() => expect(open).toBeEnabled());
    fireEvent.click(open);
    expect(screen.getByText(/Looks used so far: 2\. This makes 3\./)).toBeInTheDocument();
    const take = screen.getByRole("button", { name: "Take the final exam" });
    expect(take).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Confirmation phrase" }), { target: { value: "ma_crossover" } });
    expect(take).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Confirmation phrase" }), { target: { value: " SPEND ONE LOOK " } });
    fireEvent.click(take);

    await waitFor(() => expect(onExamStarted).toHaveBeenCalledTimes(1));
    expect(asked.filter((line) => line.startsWith("POST"))).toEqual([
      'POST /api/strategies/ma_crossover/final-exam {"typed":" SPEND ONE LOOK "}',
    ]);
  });
});
