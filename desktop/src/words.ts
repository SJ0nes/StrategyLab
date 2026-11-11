/**
 * How the window words what the server reports. Wording only: nothing here
 * computes a figure.
 */
import type { Buried, NoiseGate, RejectionReason } from "./api";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2018-12-31" as "31 Dec 2018". Text that is not an ISO date is returned as it is. */
export function dayLabel(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (match === null) {
    return iso;
  }
  const month = MONTHS[Number(match[2]) - 1];
  return month === undefined ? iso : `${Number(match[3])} ${month} ${match[1]}`;
}

/** An ISO instant as "10 Nov 2026, 18:40" (UTC, as recorded). Null is "date not recorded". */
export function whenLabel(iso: string | null): string {
  if (iso === null) {
    return "date not recorded";
  }
  const time = /T(\d{2}:\d{2})/.exec(iso);
  return time === null ? dayLabel(iso) : `${dayLabel(iso)}, ${time[1]}`;
}

/** A version's settings as "fast 10, slow 80, trend 150". */
export function versionLabel(params: Record<string, number> | null): string {
  if (params === null) {
    return "none";
  }
  return Object.entries(params)
    .map(([name, value]) => `${name} ${value}`)
    .join(", ");
}

/** A signed percentage to one decimal place, with a true minus sign. */
export function signedPct(value: number): string {
  return `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(1)}%`;
}

/** A whole amount of money with thousands separators. */
export function money(value: number): string {
  return Math.round(value).toLocaleString("en-GB");
}

/** Each reason a search turns a version down, as a short label. One per reason the server has. */
export const REASON_LABEL: Record<RejectionReason, string> = {
  edge_winner: "On the edge",
  degenerate_plateau: "Neighbours all alike",
  below_noise_ceiling: "Failed the luck test",
  drawdown_ceiling: "Fell too far",
  backtest_failed: "Could not be tested",
};

/** The same reasons as a sentence, with the figures recorded for the version. */
export function reasonDetail(entry: Buried): string {
  const fall = entry.max_drawdown_pct === null ? "" : ` Worst fall was ${entry.max_drawdown_pct.toFixed(1)}%.`;
  const score = entry.sharpe === null ? "" : ` Score was ${entry.sharpe.toFixed(2)}.`;
  switch (entry.reason) {
    case "edge_winner":
      return `It sat on the edge of the grid, so the versions beyond it could not be checked.${score}`;
    case "degenerate_plateau":
      return `Its neighbours all made the same trades, so they are no evidence that it is stable.${score}`;
    case "below_noise_ceiling":
      return `Its score was no better than the same search finds on scrambled prices.${score}`;
    case "drawdown_ceiling":
      return `It fell further than the limit allows.${fall}`;
    case "backtest_failed":
      return "The replay stopped early. No score exists.";
  }
}

/** The luck test's outcome as a pill: its colour and its few words. */
export function gatePill(gate: NoiseGate): { tone: "good" | "bad" | "warn" | ""; text: string } {
  switch (gate.status) {
    case "passed":
      return { tone: "good", text: `Beat ${gate.beaten} of ${gate.scoreable_trials}` };
    case "failed":
      return { tone: "bad", text: `Beat ${gate.beaten} of ${gate.scoreable_trials}, needed ${gate.required}` };
    case "undetermined":
      return { tone: "warn", text: `Not decided: ${gate.scoreable_trials} trials` };
    case "not_computed":
      return { tone: "", text: "No luck test" };
    case "no_candidate":
      return { tone: "warn", text: "No version put forward" };
  }
}

/** The luck test's outcome as a sentence a person can act on, from the counts the search recorded. */
export function gateSentence(gate: NoiseGate): string {
  switch (gate.status) {
    case "passed":
      return `It beat ${gate.beaten} of ${gate.scoreable_trials} scrambled price histories and needed ${gate.required}, so its score is more than luck alone produces.`;
    case "failed":
      return `It beat ${gate.beaten} of ${gate.scoreable_trials} scrambled price histories and needed ${gate.required}. A search this size finds a score like this on prices with no pattern in them, so it may be luck, and it cannot go on to the final exam.`;
    case "undetermined":
      return `Only ${gate.scoreable_trials} scrambled price histories were tried, too few to decide. It cannot go on to the final exam until a full luck test is run.`;
    case "not_computed":
      return "This search ran no luck test, so nothing says whether its score is more than luck.";
    case "no_candidate":
      return "The search put no version forward, so there was nothing to test.";
  }
}
