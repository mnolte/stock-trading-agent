/** The "Before you run" verdict lines of the estimate (item 100,
 * design/screens/Backtest.dc.html): one line for the budget, one for the
 * knowledge-cutoff timing. The numbers stay numeric in the API and are
 * formatted here. */

import type { BacktestEstimate } from '../types/api';
import { fmtMonthYear } from '../format';

export interface Verdict {
  ok: boolean;
  text: string;
}

function money(n: number): string {
  return `$${n.toFixed(2)}`;
}

export interface EstimateVerdicts {
  budget: Verdict | null;
  cutoff: Verdict | null;
  /** The budget can overrun at the call cap (may stop early). */
  stopEarly: Verdict | null;
}

export function estimateVerdicts(
  est: BacktestEstimate,
  start: string,
): EstimateVerdicts {
  // Budget: the estimate against what the month has left.
  const budget: Verdict =
    est.cost_usd <= est.cap_remaining_usd
      ? {
          ok: true,
          text: `Fits this month’s budget: about ${money(est.cost_usd)} of the ${money(est.cap_remaining_usd)} left.`,
        }
      : {
          ok: false,
          text:
            est.disabled_reason ??
            `Doesn’t fit this month’s budget: about ${money(est.cost_usd)} against the ${money(est.cap_remaining_usd)} left.`,
        };
  const stopEarly: Verdict | null =
    budget.ok && est.may_stop_early
      ? {
          ok: false,
          text: `May stop early at this month’s cap: every call at the cap would cost about ${money(est.cap_cost_usd)}.`,
        }
      : null;

  // Cutoff: refused when the period starts on or before a picked model's
  // cutoff; otherwise a positive line naming the latest cutoff.
  let cutoff: Verdict | null = null;
  if (est.cutoff && est.cutoff_label) {
    const model = est.cutoff_label.split('’s knowledge cutoff')[0].split("'s knowledge cutoff")[0];
    cutoff = {
      ok: false,
      // The refusal names the date and the earliest allowed start.
      text:
        est.disabled_reason ??
        `Starts before ${model}’s knowledge cutoff (${fmtMonthYear(est.cutoff)}) — the run is refused.`,
    };
  } else {
    const latest = est.strategies
      .map((s) => s.cutoff)
      .filter((c): c is string => c !== null)
      .sort()
      .pop();
    if (latest && start && start > latest) {
      const model =
        est.strategies.find((s) => s.cutoff === latest)?.model ?? '';
      cutoff = {
        ok: true,
        text: `Starts after ${model}’s knowledge cutoff (${fmtMonthYear(latest)}).`,
      };
    }
  }
  return { budget, cutoff, stopEarly };
}
