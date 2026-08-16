import type {
  CounterfactualPayload, ReplayResult, ReplayTarget,
} from "./types";

export interface CounterfactualSelection {
  month: string;
  target: ReplayTarget | null;
  pct: number;
}

/**
 * Return a replay only when it belongs to the controls currently on screen.
 *
 * A failed request leaves the last successful payload available so the month
 * and target pickers do not disappear. Those old financial results must not,
 * however, render under a newly selected month, target, or percentage.
 */
export function visibleReplay(
  data: CounterfactualPayload,
  error: string,
  selection: CounterfactualSelection,
): ReplayResult | null {
  if (error || !selection.target || data.month !== selection.month) return null;

  const replay = data.replay;
  if (!replay) return null;
  if (!replay.available) return replay;

  const replayTarget = replay.target;
  if (!replayTarget) return null;
  if (replayTarget.kind !== selection.target.kind
      || replayTarget.key !== selection.target.key
      || replay.reduction_pct !== selection.pct) {
    return null;
  }
  return replay;
}
