import { describe, expect, it } from 'vitest';

import type { UniverseRow, UniverseSyncJob, UniverseSyncResponse } from '../types/api';
import {
  amsTime,
  fmtDuration,
  isoMs,
  jobResultText,
  statusWord,
  syncAttention,
} from './universeFormat';

function row(partial: Partial<UniverseRow>): UniverseRow {
  return {
    symbol: 'AAPL',
    name: 'Apple',
    sector: 'Technology',
    added_at: null,
    status: 'eligible',
    progress: null,
    next_screen: null,
    rank: 1,
    last_close: null,
    avg_dollar_volume: null,
    return_vs_spy: null,
    fails: 0,
    failed_check: null,
    detail: '',
    held: false,
    qty: 0,
    screened: true,
    in_pool: true,
    ...partial,
  };
}

function job(partial: Partial<UniverseSyncJob>): UniverseSyncJob {
  return {
    job: 'daily_bars',
    label: 'Daily bars',
    status: 'ok',
    started_at: null,
    finished_at: null,
    duration_seconds: null,
    done: null,
    total: null,
    failures: [],
    ...partial,
  };
}

function sync(jobs: UniverseSyncJob[]): UniverseSyncResponse {
  return { jobs, history: null, max_requests: 20 };
}

describe('statusWord', () => {
  it('names the one effective status per symbol', () => {
    expect(statusWord(row({ status: 'eligible' }))).toBe('eligible');
    expect(statusWord(row({ status: 'eligible', fails: 1 }))).toBe('eligible · 1 fail');
    expect(statusWord(row({ status: 'eligible_fails', fails: 2 }))).toBe('eligible · 2 fails');
    expect(statusWord(row({ status: 'ready' }))).toBe('ready');
    expect(statusWord(row({ status: 'warming_up' }))).toBe('warming up');
    expect(statusWord(row({ status: 'excluded' }))).toBe('excluded');
    expect(statusWord(row({ status: 'held' }))).toBe('held');
  });
});

describe('fmtDuration', () => {
  it('formats seconds, minutes and hours like the design', () => {
    expect(fmtDuration(1.2)).toBe('1.2 s');
    expect(fmtDuration(38)).toBe('38 s');
    expect(fmtDuration(65)).toBe('1 min');
    expect(fmtDuration(3 * 3600 + 300)).toBe('3 h 5 min');
    expect(fmtDuration(null)).toBe('—');
  });
});

describe('isoMs / amsTime', () => {
  it('parses naive-UTC sync timestamps and renders Amsterdam time', () => {
    // 2025-01-15 13:46 UTC = 14:46 Amsterdam (CET, UTC+1).
    const iso = '2025-01-15T13:46:00Z';
    expect(isoMs(iso)).not.toBeNull();
    expect(amsTime(iso)).toBe('14:46');
    expect(amsTime('not a date')).toBe('—');
    expect(amsTime(null)).toBe('—');
  });
});

describe('syncAttention', () => {
  it('counts failed, running and late jobs for the card header', () => {
    const s = sync([
      job({ job: 'daily_bars', status: 'ok' }),
      job({ job: 'earnings', status: 'failed' }),
      job({ job: 'history', status: 'running' }),
      job({ job: 'fred', status: 'ok', late: true }),
    ]);
    expect(syncAttention(s)).toEqual({ failed: 1, running: 1, late: 1 });
  });
});

describe('jobResultText', () => {
  it('shows done of total for a finished job', () => {
    const j = job({ done: 72, total: 72 });
    expect(jobResultText(j, sync([j]))).toBe('72 of 72 symbols');
  });

  it('uses "checked" for the screen job', () => {
    const j = job({ job: 'screen', done: 72, total: 72 });
    expect(jobResultText(j, sync([j]))).toBe('72 of 72 checked');
  });

  it('lists failures with their error and retry time', () => {
    const j = job({
      status: 'partial',
      done: 71,
      total: 72,
      failures: [
        {
          what: 'ANET',
          error: 'Alpaca: no data for this symbol',
          next_retry: '2025-01-16T13:00:00',
        },
      ],
    });
    const text = jobResultText(j, sync([j]));
    expect(text).toContain('71 of 72 symbols');
    expect(text).toContain('ANET: Alpaca: no data for this symbol');
    expect(text).toContain('retry');
  });

  it('shows live history progress with the time left', () => {
    const j = job({ job: 'history', status: 'running', done: 41, total: 72 });
    const s: UniverseSyncResponse = {
      jobs: [j],
      history: { ready: 41, total: 72, eta_seconds: 120 },
      max_requests: 20,
    };
    expect(jobResultText(j, s)).toBe(
      'Loading history · 41 of 72 symbols ready · about 2 min left',
    );
  });
});
