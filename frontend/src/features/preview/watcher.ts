/**
 * Decides when a changed file is safe to reload: the new version must be
 * observed on two consecutive polls, so a plot still being written is not
 * loaded half-finished.
 */
export interface WatchState {
  shown: string | null;
  candidate: string | null;
  seen: number;
}

export const STABLE_POLLS = 2;
export const POLL_INTERVAL_MS = 2_000;

export const initialWatch = (version: string | null): WatchState => ({
  shown: version,
  candidate: null,
  seen: 0,
});

export function observe(state: WatchState, version: string): WatchState {
  if (state.shown === null) return { shown: version, candidate: null, seen: 0 };
  if (version === state.shown) return { ...state, candidate: null, seen: 0 };
  const seen = version === state.candidate ? state.seen + 1 : 1;
  if (seen >= STABLE_POLLS) return { shown: version, candidate: null, seen: 0 };
  return { ...state, candidate: version, seen };
}
