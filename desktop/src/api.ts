/**
 * The window's only route to the project: requests to the local server.
 *
 * Shapes here mirror what `src/strategylab/api/` returns. They describe data;
 * nothing in this file computes a figure.
 *
 * No request made here carries a credential. The server wants a token on every
 * API request, and the Electron main process adds it on the way out; this page
 * never sees it, so nothing that runs in the page can leak it.
 */

/** Why a figure is not available, in words a person can act on. */
export interface Missing {
  available: false;
  reason: string;
}

export interface Prices {
  available: true;
  instrument: string;
  bars: number;
  first_bar: string;
  last_bar: string;
  content_hash: string;
}

export interface NoiseGate {
  status: "no_candidate" | "not_computed" | "undetermined" | "failed" | "passed";
  beaten: number | null;
  required: number | null;
  scoreable_trials: number | null;
  summary: string;
}

export interface LatestSearch {
  artifact: string;
  generated_at: string | null;
  cells: number | null;
  candidate: Record<string, number> | null;
  sharpe: number | null;
  max_drawdown_pct: number | null;
  gate: NoiseGate;
  /** True when the strategy file has been edited since this search ran. */
  config_changed_since: boolean;
}

export interface Strategy {
  id: string;
  name: string;
  description: string;
  config: string;
  /** Nothing can be deployed yet, so every strategy is a draft. */
  state: "draft";
  latest_search: LatestSearch | null;
}

/** A file in `configs/` that would not load as a strategy, and why. */
export interface UnreadableConfig {
  config: string;
  reason: string;
}

export interface Overview {
  protocol_version: number;
  held_back_reads: number;
  prices: Prices | Missing;
  kill_switch: boolean | Missing;
  venue: { environment: string | null; key_present: boolean };
  strategies: Strategy[];
  unreadable_configs: UnreadableConfig[];
}

/** A request the server answered with an error, or could not be made at all. */
export class ApiError extends Error {}

/** Fetch JSON from the local server. */
export async function get<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path);
  } catch {
    throw new ApiError("The StrategyLab server is not answering. Close the app and open it again.");
  }
  if (!response.ok) {
    throw new ApiError(`The server refused the request for ${path} (${response.status}).`);
  }
  return (await response.json()) as T;
}

export const getOverview = (): Promise<Overview> => get<Overview>("/api/overview");

/** A search on record that could not be read, and why. */
export interface UnreadSearch {
  artifact: string;
  readable: false;
  generated_at: string | null;
  reason: string;
}

/** One entry in a strategy's list of searches. */
export type SearchEntry = (LatestSearch & { readable: true }) | UnreadSearch;

/** Why a search turned a version down. The server's closed vocabulary. */
export type RejectionReason =
  | "edge_winner"
  | "degenerate_plateau"
  | "below_noise_ceiling"
  | "drawdown_ceiling"
  | "backtest_failed";

/** One version (one combination of settings) a search tried. */
export interface Cell {
  /** Setting name to its value, in bars (trading days). */
  params: Record<string, number>;
  /** Annualised Sharpe; null when the replay failed. */
  sharpe: number | null;
  /** Largest fall, a percentage; null when the replay failed. */
  max_drawdown_pct: number | null;
  trade_count: number;
  plateau_score: number | null;
  neighbour_count: number;
  /** Total return over the window, a percentage; null when there is no curve. */
  total_return_pct: number | null;
  mark: "candidate" | RejectionReason | null;
  /** Account value at each of the search's dates; null before warm-up ends. */
  curve: (number | null)[] | null;
}

/** One search in full. */
export interface SearchDetail extends Omit<LatestSearch, "cells"> {
  available: true;
  instrument: string;
  window: { start: string; end: string; bars: number };
  starting_equity: number;
  max_drawdown_ceiling_pct: number;
  /** Setting names, in the order the strategy file declares them. */
  parameters: string[];
  /** The values each setting took, ascending. */
  axes: Record<string, number[]>;
  dates: string[];
  cells: Cell[];
  rejections: { params: Record<string, number>; reason: RejectionReason; detail: string }[];
  ceiling: {
    trials: number;
    scoreable_trials: number;
    percentiles: Record<string, number | null>;
    max: number | null;
  } | null;
}

/** One read of the held-back years ("final exam") on record. */
export type Exam =
  | {
      artifact: string;
      readable: true;
      generated_at: string | null;
      params: Record<string, number> | null;
      outcome: "pass" | "fail" | "insufficient_data";
      reason: string;
      sharpe: number | null;
      max_drawdown_pct: number | null;
      trades: number | null;
      folds: number | null;
      read_number: number | null;
    }
  | UnreadSearch;

/** One distinct rejected version, across the whole project. */
export interface Buried {
  candidate: Record<string, number>;
  reason: RejectionReason;
  times: number;
  last_recorded_at: string;
  sharpe: number | null;
  max_drawdown_pct: number | null;
}

export interface Graveyard {
  available: true;
  records: number;
  unread_lines: number;
  entries: Buried[];
}

const strategyPath = (id: string): string => `/api/strategies/${encodeURIComponent(id)}`;

export const getSearches = (id: string): Promise<SearchEntry[]> =>
  get<SearchEntry[]>(`${strategyPath(id)}/searches`);

export const getSearch = (id: string, artifact: string): Promise<SearchDetail | Missing> =>
  get<SearchDetail | Missing>(`${strategyPath(id)}/searches/${encodeURIComponent(artifact)}`);

export const getExams = (id: string): Promise<Exam[]> => get<Exam[]>(`${strategyPath(id)}/exams`);

export const getGraveyard = (): Promise<Graveyard | Missing> => get<Graveyard | Missing>("/api/graveyard");
