import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Settlement, Strategy } from "../src/api";
import { SettleCard, signedUnits, unitWord } from "../src/components/SettleCard";
import { whenLabel } from "../src/words";

const STRATEGY = { id: "ma_crossover", name: "ma_crossover" } as Strategy;

// The operator's example: the records say 10, Trading 212 holds 8.
const SETTLE: Settlement = {
  state: "settle",
  why: "the venue holds 8 units of VUAG.L and the strategies' recorded fills add up to 10",
  venue_units: 8,
  recorded_units: 10,
  difference: -2,
  snapshot_at: "2026-11-11T17:45:00+00:00",
  ticker: "VUAG.L",
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("settling a disagreement", () => {
  it("writes units signed, without trailing zeros", () => {
    expect(signedUnits(-2)).toBe("−2");
    expect(signedUnits(0.5)).toBe("+0.5");
    expect(signedUnits(1.23456789)).toBe("+1.234568");
    expect([unitWord(-1), unitWord(1), unitWord(2), unitWord(0.5)]).toEqual(["unit", "unit", "units", "units"]);
  });

  it("shows nothing while the records and Trading 212 agree", () => {
    const { container } = render(<SettleCard strategy={STRATEGY} settlement={{ state: "agree" }} onChanged={vi.fn()} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("says why it cannot be read, or cannot be settled yet, and offers nothing to press", () => {
    const { rerender } = render(
      <SettleCard strategy={STRATEGY} settlement={{ state: "unreadable", why: "the logs could not be read" }} onChanged={vi.fn()} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("the logs could not be read");

    rerender(
      <SettleCard
        strategy={STRATEGY}
        settlement={{ ...SETTLE, state: "wait", why: "something was recorded after the newest check; run a check first" }}
        onChanged={vi.fn()}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("It cannot be settled yet: something was recorded after the newest check; run a check first.");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("gives the difference to the strategy once its name is typed, sending the figure shown", async () => {
    const asked: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string, init?: RequestInit) => {
        asked.push(`${init?.method ?? "GET"} ${path} ${String(init?.body ?? "")}`);
        return new Response(JSON.stringify({ state: "agree" }));
      }),
    );
    const onChanged = vi.fn();
    render(<SettleCard strategy={STRATEGY} settlement={SETTLE} onChanged={onChanged} />);

    const card = screen.getByRole("alert");
    expect(card).toHaveTextContent(`At the check ${whenLabel(SETTLE.snapshot_at)}, Trading 212 held 8 units of VUAG.L`);
    expect(card).toHaveTextContent("add up to 10. The difference is −2 units.");
    fireEvent.click(screen.getByRole("button", { name: "Give the difference to ma_crossover…" }));
    const give = screen.getByRole("button", { name: "Give it to ma_crossover" });
    expect(screen.getByRole("dialog")).toHaveTextContent("Give −2 units to ma_crossover");
    // Beside the card, not in it, so the card's styles do not reach the dialog.
    expect(card).not.toContainElement(screen.getByRole("dialog"));
    expect(give).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Strategy name" }), { target: { value: "ma_cross" } });
    expect(give).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Strategy name" }), { target: { value: " ma_crossover " } });
    fireEvent.click(give);

    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    // What was typed is sent as typed; the server decides whether it matches.
    expect(asked).toEqual(['POST /api/strategies/ma_crossover/settle {"typed":" ma_crossover ","units":-2}']);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows the server's refusal and keeps the dialog open", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "the difference has changed since it was shown; read it again" }), { status: 409 })),
    );
    const onChanged = vi.fn();
    render(<SettleCard strategy={STRATEGY} settlement={SETTLE} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole("button", { name: "Give the difference to ma_crossover…" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Strategy name" }), { target: { value: "ma_crossover" } });
    fireEvent.click(screen.getByRole("button", { name: "Give it to ma_crossover" }));

    expect(await screen.findByText("Refused: the difference has changed since it was shown; read it again.")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(onChanged).not.toHaveBeenCalled();
  });
});
