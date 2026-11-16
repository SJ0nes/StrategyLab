import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";
import type { Overview } from "../src/api";
import { STOPS, Tour, place } from "../src/components/Tour";

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
      config_hash: "a".repeat(64),
      state: "draft",
      latest_search: null,
      rules: { entry: ["Buy rule."], exit: ["Sell rule."], search: [], fixed: [], versions: 1 },
      source: null,
      confirmed: false,
      confirmed_at: null,
      stale: false,
    },
  ],
  unreadable_configs: [],
  tour_seen: false,
};

function serve(overview: Overview): string[] {
  const asked: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      asked.push(`${init?.method ?? "GET"} ${path}`);
      if (path === "/api/overview") {
        return new Response(JSON.stringify(overview));
      }
      if (path === "/api/jobs") {
        return new Response(JSON.stringify({ current: null, recent: [] }));
      }
      return new Response(JSON.stringify({ tour_seen: true }));
    }),
  );
  return asked;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("placing the tip", () => {
  const screenSize = { width: 1366, height: 768 };
  const tip = { width: 368, height: 200 };

  it("puts the tip below an element when it fits, and the spotlight around it", () => {
    const placed = place({ left: 100, top: 50, width: 300, height: 40 }, tip, screenSize);

    expect(placed.spot).toEqual({ left: 92, top: 42, width: 316, height: 56 });
    expect(placed.tip).toEqual({ left: 100, top: 112 });
  });

  it("puts it to the right when there is no room below", () => {
    const placed = place({ left: 100, top: 400, width: 300, height: 300 }, tip, screenSize);

    // Below would end at 722 + 200, past 768; to the right there is room.
    expect(placed.tip).toEqual({ left: 422, top: 400 });
  });

  it("never puts it off the window", () => {
    const placed = place({ left: 1300, top: 700, width: 60, height: 60 }, tip, screenSize);

    expect(placed.tip.left).toBeGreaterThanOrEqual(16);
    expect(placed.tip.left + tip.width).toBeLessThanOrEqual(screenSize.width - 16);
    expect(placed.tip.top + tip.height).toBeLessThanOrEqual(screenSize.height - 16);
  });

  it("pulls a tip back inside when the element sits near the right edge", () => {
    const placed = place({ left: 1200, top: 50, width: 100, height: 30 }, tip, screenSize);

    expect(placed.tip).toEqual({ left: 1366 - 368 - 16, top: 102 });
  });

  it("lights the middle of the window when the element is not there", () => {
    const placed = place(null, tip, screenSize);

    expect(placed.spot).toEqual({ left: 675, top: 376, width: 16, height: 16 });
  });
});

describe("the stops", () => {
  it("say nothing the app does not do", () => {
    const words = STOPS.map((stop) => `${stop.title} ${stop.text}`).join(" ").toLowerCase();

    expect(words).not.toMatch(/buy.and.hold|benchmark|automatically every morning|win rate/);
    expect(words).toContain("strategylab fetch");
    expect(words).toContain("draft");
  });

  it("each light something the window draws", () => {
    expect(STOPS.map((stop) => stop.target)).toEqual(["pick", "prices", "rules", "path", "live", "lab", "help"]);
  });
});

describe("the tour", () => {
  it("walks forward and back, and moves the window to each stop's section", () => {
    const onGo = vi.fn();
    const onDraft = vi.fn();
    const onEnd = vi.fn();
    render(<Tour onGo={onGo} onDraft={onDraft} onEnd={onEnd} />);

    expect(screen.getByText(`1 of ${STOPS.length}`)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Back" })).toBeNull();
    for (let step = 1; step < STOPS.length; step += 1) {
      fireEvent.click(screen.getByRole("button", { name: "Next" }));
    }
    expect(screen.getByText(`${STOPS.length} of ${STOPS.length}`)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("heading", { name: STOPS[STOPS.length - 2]?.title })).toBeInTheDocument();
    expect(onGo.mock.calls.map((call) => call[0])).toContain("improve");
    // A draft is shown for the stops about rules and the path, and only for those.
    expect(onDraft.mock.calls.map((call) => call[0])).toEqual([
      ...STOPS.map((stop) => ["rules", "path"].includes(stop.target)),
      false,
    ]);
    expect(onEnd).not.toHaveBeenCalled();
  });

  it("says it was finished, or skipped", () => {
    const onEnd = vi.fn();
    const { unmount } = render(<Tour onGo={vi.fn()} onDraft={vi.fn()} onEnd={onEnd} />);
    for (let step = 1; step < STOPS.length; step += 1) {
      fireEvent.click(screen.getByRole("button", { name: "Next" }));
    }
    fireEvent.click(screen.getByRole("button", { name: "Finish" }));
    unmount();
    render(<Tour onGo={vi.fn()} onDraft={vi.fn()} onEnd={onEnd} />);
    fireEvent.click(screen.getByRole("button", { name: "Skip" }));

    expect(onEnd.mock.calls).toEqual([[true], [false]]);
  });
});

describe("the app's first launch", () => {
  it("offers the tour once, and tells the server when it is skipped", async () => {
    const asked = serve(OVERVIEW);
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Skip" }));

    await waitFor(() => expect(asked).toContain("POST /api/tour/seen"));
    expect(screen.queryByText("Welcome to StrategyLab")).toBeNull();
  });

  it("offers no tour once the server says it has been seen, and replays it from the top bar", async () => {
    const asked = serve({ ...OVERVIEW, tour_seen: true });
    render(<App />);
    await screen.findByRole("heading", { name: /ma_crossover/ });

    expect(screen.queryByText("Welcome to StrategyLab")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "▶ Tour" }));
    expect(screen.getByText(`1 of ${STOPS.length}`)).toBeInTheDocument();
    expect(asked).not.toContain("POST /api/tour/seen");
  });

  it("starts the tour from the welcome, and marks it seen at the end", async () => {
    const asked = serve(OVERVIEW);
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Show me around" }));
    await act(async () => {
      for (let step = 1; step < STOPS.length; step += 1) {
        fireEvent.click(screen.getByRole("button", { name: "Next" }));
      }
    });
    fireEvent.click(screen.getByRole("button", { name: "Finish" }));

    await waitFor(() => expect(asked).toContain("POST /api/tour/seen"));
    expect(window.location.hash).toBe("#dashboard");
  });

  it("returns to the Dashboard when skipped from another section", async () => {
    serve(OVERVIEW);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Show me around" }));
    const toWorkshop = STOPS.findIndex((stop) => stop.go === "improve");
    for (let step = 0; step < toWorkshop; step += 1) {
      fireEvent.click(screen.getByRole("button", { name: "Next" }));
    }
    expect(window.location.hash).toBe("#improve");

    fireEvent.click(screen.getByRole("button", { name: "Skip" }));

    expect(window.location.hash).toBe("#dashboard");
  });

  it("moves one stop per press however fast the presses come", () => {
    render(<Tour onGo={vi.fn()} onDraft={vi.fn()} onEnd={vi.fn()} />);

    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Next" }));
      fireEvent.click(screen.getByRole("button", { name: "Next" }));
    });

    expect(screen.getByText(`3 of ${STOPS.length}`)).toBeInTheDocument();
  });
});
