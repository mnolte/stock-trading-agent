import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { get, post } from '../api/client';
import { useLiveUpdates } from '../api/liveContext';

interface LogLine {
  id: number;
  ts: string;
  level: string;
  logger: string;
  event: string | null;
  message: string;
  context: Record<string, unknown>;
  cycle: string | null;
  symbol: string | null;
  process: string;
  pid: number;
  error_id: string | null;
  exception_type: string | null;
  exception_message: string | null;
  traceback: string | null;
  fingerprint: string;
}

interface Problem {
  fingerprint: string;
  level: string;
  last_ts: string;
  count: number;
  sample: string;
  review: string | null;
}

const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const;
const DEFAULT_LEVELS = ['WARNING', 'ERROR', 'CRITICAL'];
const PERIODS = ['Last hour', 'Today', '7 days', '30 days', 'Custom'] as const;
const PERIOD_DAYS: Record<string, number> = { '7 days': 7, '30 days': 30 };

function parseLevels(param: string | null): string[] {
  if (!param) return DEFAULT_LEVELS;
  const lv = param.split(',').filter((l) => LEVELS.includes(l as never));
  return lv.length ? lv : DEFAULT_LEVELS;
}

function periodFrom(period: string): string | null {
  const now = new Date();
  if (period === 'Last hour') return new Date(now.getTime() - 3600_000).toISOString();
  if (period === 'Today') return new Date(now.setHours(0, 0, 0, 0)).toISOString();
  const days = PERIOD_DAYS[period];
  if (days) return new Date(now.getTime() - days * 86400_000).toISOString();
  return null;
}

function dayOf(ts: string): string {
  return ts.slice(0, 10);
}

export default function Logs() {
  const [params, setParams] = useSearchParams();
  const levels = useMemo(
    () => parseLevels(params.get('levels')),
    [params],
  );
  const [period, setPeriod] = useState('7 days');
  const [search, setSearch] = useState('');
  const [loggerFilter, setLoggerFilter] = useState('');
  const [symbolFilter, setSymbolFilter] = useState(params.get('symbol') ?? '');
  const [cycleFilter, setCycleFilter] = useState(params.get('cycle') ?? '');
  const [moreFilters, setMoreFilters] = useState(false);
  const [lines, setLines] = useState<LogLine[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [summary, setSummary] = useState<{
    days: { day: string; level: string; count: number }[];
    problems: Problem[];
    index_available: boolean;
  } | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [around, setAround] = useState<Record<number, LogLine[]>>({});
  const [follow, setFollow] = useState(true);
  const listRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);

  // ?levels= persists per URL + per-browser memory.
  useEffect(() => {
    localStorage.setItem('sa-log-levels', levels.join(','));
  }, [levels]);
  useEffect(() => {
    if (!params.get('levels')) {
      const saved = localStorage.getItem('sa-log-levels');
      if (saved) setParams({ levels: saved }, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleLevel = (level: string) => {
    const next = levels.includes(level)
      ? levels.filter((l) => l !== level)
      : [...levels, level];
    setParams({ levels: (next.length ? next : DEFAULT_LEVELS).join(',') });
  };
  const showAll = levels.length === LEVELS.length;
  const toggleAll = () =>
    setParams({ levels: showAll ? DEFAULT_LEVELS.join(',') : LEVELS.join(',') });

  const buildQuery = useCallback(
    (extra?: Record<string, string>) => {
      const q = new URLSearchParams();
      q.set('levels', levels.join(','));
      const from = periodFrom(period);
      if (from) q.set('from', from);
      if (search.trim()) q.set('search', search.trim());
      if (loggerFilter.trim()) q.set('logger', loggerFilter.trim());
      if (symbolFilter.trim()) q.set('symbol', symbolFilter.trim());
      if (cycleFilter.trim()) q.set('cycle', cycleFilter.trim());
      if (extra) for (const [k, v] of Object.entries(extra)) q.set(k, v);
      return q.toString();
    },
    [levels, period, search, loggerFilter, symbolFilter, cycleFilter],
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await get<{
        lines: LogLine[];
        next_cursor: number | null;
        index_available: boolean;
      }>(`/api/logs?${buildQuery()}&limit=200`);
      setLines(data.lines);
      setCursor(data.next_cursor);
      const s = await get<typeof summary & object>(
        `/api/logs/summary`,
      );
      setSummary(s as typeof summary);
    } finally {
      setLoading(false);
    }
  }, [buildQuery]);

  useEffect(() => {
    void load();
  }, [load]);

  // Follow new lines: the "logs" topic pings on warning-and-up.
  useLiveUpdates(['logs'], () => {
    if (follow) void load();
  });

  const onScroll = () => {
    const el = listRef.current;
    if (!el) return;
    // Following pauses when scrolled away from the top (newest first).
    setFollow(el.scrollTop < 40);
  };

  const openLine = async (line: LogLine) => {
    if (expanded === line.id) {
      setExpanded(null);
      return;
    }
    setExpanded(line.id);
    if (!around[line.id]) {
      const data = await get<{ lines: LogLine[] }>(
        `/api/logs/${line.id}/around?window=10`,
      );
      setAround((prev) => ({ ...prev, [line.id]: data.lines }));
    }
  };

  const review = async (problem: Problem, kind: string) => {
    const note = window.prompt(
      `Mark this problem as ${kind}? A note is required.`,
    );
    if (!note) return;
    await post('/api/logs/review', {
      fingerprint: problem.fingerprint,
      review: kind,
      note,
      level: problem.level,
      sample: problem.sample,
    });
    await load();
  };

  const clearFilters = () => {
    setSearch('');
    setLoggerFilter('');
    setSymbolFilter('');
    setCycleFilter('');
    setPeriod('7 days');
  };

  const grouped = useMemo(() => {
    const out = new Map<string, LogLine[]>();
    for (const line of lines) {
      const day = dayOf(line.ts);
      if (!out.has(day)) out.set(day, []);
      out.get(day)!.push(line);
    }
    return [...out.entries()];
  }, [lines]);

  const dayCounts = useMemo(() => {
    const map = new Map<string, { warning: number; error: number }>();
    for (const d of summary?.days ?? []) {
      const row = map.get(d.day) ?? { warning: 0, error: 0 };
      if (d.level === 'WARNING') row.warning += d.count;
      else row.error += d.count;
      map.set(d.day, row);
    }
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [summary]);

  const maxCount = Math.max(
    1,
    ...dayCounts.map(([, c]) => c.warning + c.error),
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-4)', padding: 'var(--sa-space-4)', height: '100%', overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sa-space-3)', flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 'var(--sa-text-xl)', fontWeight: 600, margin: 0 }}>Logs</h1>
        {LEVELS.filter((l) => l !== 'DEBUG' && l !== 'INFO').map((level) => (
          <button
            key={level}
            type="button"
            onClick={() => toggleLevel(level)}
            aria-pressed={levels.includes(level)}
            style={{
              padding: '2px var(--sa-space-3)',
              borderRadius: 'var(--sa-radius-md)',
              border: `1px solid ${levels.includes(level) ? 'var(--sa-accent)' : 'var(--sa-border)'}`,
              background: levels.includes(level) ? 'var(--sa-raised)' : 'transparent',
              color: 'var(--sa-text)', fontSize: 'var(--sa-text-sm)', cursor: 'pointer',
            }}
          >
            {level === 'CRITICAL' ? 'Critical' : level === 'ERROR' ? 'Error' : 'Warning'}
          </button>
        ))}
        <label style={{ display: 'flex', alignItems: 'center', gap: 'var(--sa-space-2)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-2)', cursor: 'pointer' }}>
          <input type="checkbox" checked={showAll} onChange={toggleAll} />
          Show debug and info
        </label>
        <select
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
          aria-label="Period"
          style={{ height: 'var(--sa-control-height)', borderRadius: 'var(--sa-radius-md)', border: '1px solid var(--sa-border)', background: 'var(--sa-raised)', color: 'var(--sa-text)', padding: '0 var(--sa-space-2)' }}
        >
          {PERIODS.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search messages"
          aria-label="Search messages"
          style={{ height: 'var(--sa-control-height)', borderRadius: 'var(--sa-radius-md)', border: '1px solid var(--sa-border)', background: 'var(--sa-raised)', color: 'var(--sa-text)', padding: '0 var(--sa-space-3)', minWidth: 160 }}
        />
        <button type="button" className="sa-more-filters" onClick={() => setMoreFilters((v) => !v)} style={{ display: 'none' }}>
          More filters
        </button>
        <button type="button" onClick={clearFilters} style={{ height: 'var(--sa-control-height)', padding: '0 var(--sa-space-3)', borderRadius: 'var(--sa-radius-md)', border: '1px solid var(--sa-border)', background: 'transparent', color: 'var(--sa-text-2)', cursor: 'pointer' }}>
          Clear filters
        </button>
        <a
          href={`/api/logs/export.jsonl?${buildQuery()}`}
          download="logs.jsonl"
          style={{ height: 'var(--sa-control-height)', display: 'inline-flex', alignItems: 'center', padding: '0 var(--sa-space-3)', borderRadius: 'var(--sa-radius-md)', border: '1px solid var(--sa-border)', color: 'var(--sa-text-2)', textDecoration: 'none' }}
        >
          Download
        </a>
      </div>

      <div className="sa-log-filters" data-more={moreFilters} style={{ display: 'flex', gap: 'var(--sa-space-2)', flexWrap: 'wrap' }}>
        <input value={loggerFilter} onChange={(e) => setLoggerFilter(e.target.value)} placeholder="Component" aria-label="Component" style={{ height: 'var(--sa-control-height)', borderRadius: 'var(--sa-radius-md)', border: '1px solid var(--sa-border)', background: 'var(--sa-raised)', color: 'var(--sa-text)', padding: '0 var(--sa-space-3)', width: 130 }} />
        <input value={symbolFilter} onChange={(e) => setSymbolFilter(e.target.value)} placeholder="Symbol" aria-label="Symbol" style={{ height: 'var(--sa-control-height)', borderRadius: 'var(--sa-radius-md)', border: '1px solid var(--sa-border)', background: 'var(--sa-raised)', color: 'var(--sa-text)', padding: '0 var(--sa-space-3)', width: 90 }} />
        <input value={cycleFilter} onChange={(e) => setCycleFilter(e.target.value)} placeholder="Cycle" aria-label="Cycle" style={{ height: 'var(--sa-control-height)', borderRadius: 'var(--sa-radius-md)', border: '1px solid var(--sa-border)', background: 'var(--sa-raised)', color: 'var(--sa-text)', padding: '0 var(--sa-space-3)', width: 110 }} />
      </div>

      {dayCounts.length > 0 && (
        <div aria-label="Warnings and errors per day" style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 48, borderBottom: '1px solid var(--sa-border)' }}>
          {dayCounts.map(([day, c]) => (
            <div key={day} title={`${day}: ${c.warning} warnings, ${c.error} errors`} style={{ display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', width: 14, height: '100%' }}>
              <div style={{ height: `${(c.error / maxCount) * 100}%`, background: 'var(--sa-negative)', minHeight: c.error ? 2 : 0 }} />
              <div style={{ height: `${(c.warning / maxCount) * 100}%`, background: 'var(--sa-warning)', minHeight: c.warning ? 2 : 0 }} />
            </div>
          ))}
        </div>
      )}

      {(summary?.problems.length ?? 0) > 0 && (
        <section aria-label="Recurring problems" style={{ border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-md)', padding: 'var(--sa-space-3)' }}>
          <div style={{ fontWeight: 600, marginBottom: 'var(--sa-space-2)' }}>Recurring problems</div>
          {summary!.problems.map((p) => (
            <div key={p.fingerprint} style={{ display: 'flex', gap: 'var(--sa-space-3)', alignItems: 'baseline', fontSize: 'var(--sa-text-sm)', padding: '2px 0' }}>
              <span style={{ color: p.level === 'CRITICAL' ? 'var(--sa-negative)' : 'var(--sa-warning)', fontWeight: 600 }}>{p.level}</span>
              <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.sample}</span>
              <span style={{ color: 'var(--sa-text-muted)' }}>×{p.count}</span>
              {p.review ? (
                <span style={{ color: 'var(--sa-text-muted)' }}>{p.review}</span>
              ) : (
                <span>
                  <button type="button" onClick={() => review(p, 'expected')} style={{ marginRight: 4, fontSize: 'var(--sa-text-xs)' }}>Expected</button>
                  <button type="button" onClick={() => review(p, 'fixed')} style={{ fontSize: 'var(--sa-text-xs)' }}>Fixed</button>
                </span>
              )}
            </div>
          ))}
        </section>
      )}

      <div ref={listRef} onScroll={onScroll} role="log" aria-label="Log lines" style={{ flex: 1, overflowY: 'auto', fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-xs)' }}>
        {loading && <div style={{ color: 'var(--sa-text-muted)' }}>Loading…</div>}
        {!loading && lines.length === 0 && (
          <div style={{ color: 'var(--sa-text-muted)' }}>No lines match the filters.</div>
        )}
        {grouped.map(([day, dayLines]) => (
          <div key={day}>
            <div style={{ position: 'sticky', top: 0, background: 'var(--sa-bg)', color: 'var(--sa-text-muted)', padding: '4px 0', fontWeight: 600, zIndex: 1 }}>{day}</div>
            {dayLines.map((line) => (
              <div key={line.id}>
                <button
                  type="button"
                  onClick={() => openLine(line)}
                  style={{
                    display: 'flex', gap: 'var(--sa-space-2)', width: '100%', textAlign: 'left',
                    background: 'none', border: 'none', padding: '1px 0', cursor: 'pointer',
                    fontFamily: 'inherit', fontSize: 'inherit',
                    color: line.level === 'CRITICAL' || line.level === 'ERROR'
                      ? 'var(--sa-negative)'
                      : line.level === 'WARNING'
                        ? 'var(--sa-warning)'
                        : 'var(--sa-text-2)',
                  }}
                >
                  <span style={{ color: 'var(--sa-text-muted)', flexShrink: 0 }}>{line.ts.slice(11, 23)}</span>
                  <span style={{ width: 70, flexShrink: 0 }}>{line.level}</span>
                  <span style={{ width: 110, flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{line.logger}</span>
                  <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{line.message.split('\n')[0]}</span>
                  {line.error_id && <span style={{ color: 'var(--sa-text-muted)' }}>{line.error_id}</span>}
                </button>
                {expanded === line.id && (
                  <div style={{ margin: '4px 0 8px 190px', padding: 'var(--sa-space-3)', background: 'var(--sa-raised)', borderRadius: 'var(--sa-radius-md)', whiteSpace: 'pre-wrap', color: 'var(--sa-text-2)' }}>
                    {line.traceback && <div style={{ marginBottom: 'var(--sa-space-2)' }}>{line.traceback}</div>}
                    <div style={{ color: 'var(--sa-text-muted)' }}>{JSON.stringify(line)}</div>
                    {(around[line.id] ?? []).map((a) => (
                      <div key={a.id} style={{ color: a.id === line.id ? 'var(--sa-text)' : 'var(--sa-text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {a.ts.slice(11, 23)} {a.level} {a.message.split('\n')[0]}
                      </div>
                    ))}
                    {line.cycle && (
                      <a href={`/logs?cycle=${line.cycle}`} style={{ color: 'var(--sa-accent)' }}>
                        Related: cycle {line.cycle}
                      </a>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        ))}
      </div>
      {cursor !== null && (
        <button type="button" onClick={async () => {
          const data = await get<{ lines: LogLine[]; next_cursor: number | null }>(
            `/api/logs?${buildQuery({ cursor: String(cursor) })}&limit=200`,
          );
          setLines((prev) => [...prev, ...data.lines]);
          setCursor(data.next_cursor);
        }}>
          Load older
        </button>
      )}
      {!follow && (
        <button type="button" onClick={() => { setFollow(true); listRef.current?.scrollTo({ top: 0 }); }} style={{ position: 'sticky', bottom: 0 }}>
          Follow new lines
        </button>
      )}
    </div>
  );
}
