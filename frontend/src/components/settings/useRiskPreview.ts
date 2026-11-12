import { useEffect, useRef, useState } from 'react';

import { postSettingsPreview } from '../../api/state';
import type { SettingsForm, SettingsPreview } from '../../types/api';

/** Debounced preview of the draft ``risk`` subtree. */
export function useRiskPreview(
  risk: SettingsForm['risk'] | null,
): SettingsPreview | null {
  const [preview, setPreview] = useState<SettingsPreview | null>(null);
  const riskJson = risk ? JSON.stringify(risk) : '';
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!riskJson) return;
    const body = { risk: JSON.parse(riskJson) as Record<string, unknown> };
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      postSettingsPreview(body)
        .then(setPreview)
        .catch(() => {
          // Keep the last answer — a failed refresh must not blank the
          // amounts (logged by the api client already).
        });
    }, 300);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [riskJson]);

  return preview;
}


const usd = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
});

export function fmtUsd(value: string | number): string {
  const n = typeof value === 'string' ? Number(value) : value;
  return Number.isFinite(n) ? usd.format(n) : '—';
}
