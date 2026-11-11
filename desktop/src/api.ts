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
  scoreable_trials: number;
  summary: string;
}

export interface LatestSearch {
  artifact: string;
  generated_at: string;
  cells: number;
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
