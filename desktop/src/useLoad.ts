import { useEffect, useState } from "react";

import {
  ApiError,
  type Missing,
  type SearchDetail,
  type SearchEntry,
  getSearch,
  getSearches,
} from "./api";

export type Load<T> = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: T };

/**
 * Run a request when `key` changes, and report where it has got to.
 *
 * An answer that arrives after `key` has changed again is dropped, so a slow
 * request for one strategy cannot overwrite a newer one's screen.
 */
export function useLoad<T>(key: string, request: () => Promise<T>): Load<T> {
  const [load, setLoad] = useState<Load<T>>({ state: "loading" });

  useEffect(() => {
    let current = true;
    setLoad({ state: "loading" });
    request()
      .then((value) => {
        if (current) {
          setLoad({ state: "ready", value });
        }
      })
      .catch((error: unknown) => {
        if (current) {
          setLoad({
            state: "failed",
            message: error instanceof ApiError ? error.message : "The app could not read the project.",
          });
        }
      });
    return () => {
      current = false;
    };
    // `request` is rebuilt on every render; `key` is what says the request changed.
  }, [key]);

  return load;
}

/** A strategy's searches, and the newest readable one in full (null when there is none). */
export interface Searches {
  list: SearchEntry[];
  newest: SearchDetail | Missing | null;
}

/** Fetch a strategy's list of searches, then the newest one that could be read. */
export async function loadSearches(strategyId: string): Promise<Searches> {
  const list = await getSearches(strategyId);
  const first = list.find((entry) => entry.readable);
  return { list, newest: first === undefined ? null : await getSearch(strategyId, first.artifact) };
}
