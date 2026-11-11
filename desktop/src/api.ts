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

/** A strategy's rules, worded by the server from what the loader read. */
export interface Rules {
  entry: string[];
  exit: string[];
  search: string[];
  fixed: string[];
  /** How many versions a search of it tries. */
  versions: number;
}

/** Where a strategy read out of a video came from, as its source file records it. */
export interface Source {
  available: true;
  /** An https link, shown as text and never opened; null when there is none. */
  url: string | null;
  title: string | null;
  channel: string | null;
  watched_at: string | null;
  rules: { rule: string; at: string | null }[];
  left_out: { what: string; why: string | null; at: string | null }[];
}

export interface Strategy {
  id: string;
  name: string;
  description: string;
  config: string;
  /** Content hash of the config the rules below were worded from. */
  config_hash: string;
  /** Nothing can be deployed yet, so every strategy is a draft. */
  state: "draft";
  latest_search: LatestSearch | null;
  rules: Rules;
  /** Null for a strategy written by hand. */
  source: Source | Missing | null;
  /** True only when the rules were confirmed for the file as it is now. */
  confirmed: boolean;
  confirmed_at: string | null;
  /** True when a confirmation is on record for an earlier version of the file. */
  stale: boolean;
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

/** The kinds of work the window can start. The server's closed list. */
export type JobKind = "search" | "luck_test";

/** One piece of work started from the window, and where it has got to. */
export interface Job {
  id: string;
  kind: JobKind;
  strategy_id: string | null;
  trials: number | null;
  started_at: string | null;
  status: "running" | "stopping" | "finished" | "stopped" | "failed" | "lost";
  /** Scrambled histories finished so far; null when the job reports none. */
  progress: { finished: number; total: number } | null;
  last_line: string | null;
}

export interface Jobs {
  /** The job that is running or stopping, if any. Only one runs at a time. */
  current: Job | null;
  recent: Job[];
}

/** Ask the server to do something. A refusal's own words become the error's message. */
export async function post<T>(path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(
      path,
      body === undefined
        ? { method: "POST" }
        : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
    );
  } catch {
    throw new ApiError("The StrategyLab server is not answering. Close the app and open it again.");
  }
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail =
      typeof body === "object" && body !== null && "detail" in body && typeof body.detail === "string"
        ? body.detail
        : null;
    throw new ApiError(
      detail === null ? `The server refused the request (${response.status}).` : `Refused: ${detail}.`,
    );
  }
  return (await response.json()) as T;
}

export const getJobs = (): Promise<Jobs> => get<Jobs>("/api/jobs");

export const startJob = (id: string, kind: JobKind): Promise<Job> =>
  post<Job>(`${strategyPath(id)}/jobs/${kind}`);

export const stopJob = (jobId: string): Promise<Job> => post<Job>(`/api/jobs/${encodeURIComponent(jobId)}/stop`);

/**
 * Record that the rules on screen have been read and are right. The hash the
 * rules were shown for goes with the request: if the file has changed since,
 * the server refuses, and nobody approves rules they did not see.
 */
export const confirmStrategy = (id: string, configHash: string): Promise<{ confirmed: boolean }> =>
  post<{ confirmed: boolean }>(`${strategyPath(id)}/confirm`, { config_hash: configHash });
