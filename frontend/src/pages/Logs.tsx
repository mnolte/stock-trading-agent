import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { errorMessages, get, post } from '../api/client';
import { useLiveUpdates } from '../api/liveContext';
import { Card } from '../components/Card';
import { Chip } from '../components/Chip';
import { DialogButton, Modal } from '../components/Modal';
import { FetchError, SkeletonLines } from '../components/cardState';
import { Toggle } from '../components/Toggle';
import { displayDayTime } from '../format';

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
  first_ts: string | null;
  last_ts: string;
  count: number;
  sample: string;
  review: string | null;
}

interface Summary {
  days: { day: string; level: string; count: number }[];
  level_counts: Record<string, number>;
  problems: Problem[];
  index_available: boolean;
}

const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const;
const DEFAULT_LEVELS = ['WARNING', 'ERROR', 'CRITICAL'];
const PERIODS = ['Last hour', 'Today', '7 days', '30 days', 'Custom'] as const;
const PERIOD_DAYS: Record<string, number> = { '7 days': 7, '30 days': 30 };

const LEVEL_COLOR: Record<string, string> = {
  DEBUG: 'var(--sa-neutral-chip)',
  INFO: 'var(--sa-info)',
  WARNING: 'var(--sa-accent)',
  ERROR: 'var(--sa-negative)',
  CRITICAL: '#FF3B5C',
};

const LEVEL_CHIP: Record<string, 'neutral' | 'info' | 'warning' | 'negative'> = {
  DEBUG: 'neutral',
  INFO: 'info',
  WARNING: 'warning',
  ERROR: 'negative',
  CRITICAL: 'negative',
};

function parseLevels(param: string | null): string[] {
  if (!param) return DEFAULT_LEVELS;
  const lv = param.split(',').filter((l) => LEVELS.includes(l as never));
  return lv.length ? lv : DEFAULT_LEVELS;
}

function periodFrom(period: string, customFrom: string, customTo: string): { from: string | null; to: string | null } {
  const now = new Date();
  if (period === 'Custom') {
    return {
      from: customFrom ? new Date(`${customFrom}T00:00:00`).toISOString() : null,
      to: customTo ? new Date(`${customTo}T23:59:59.999`).toISOString() : null,
    };
  }
  if (period === 'Last hour') return { from: new Date(now.getTime() - 3600_000).toISOString(), to: null };
  if (period === 'Today') return { from: new Date(now.setHours(0, 0, 0, 0)).toISOString(), to: null };
  const days = PERIOD_DAYS[period];
  if (days) return { from: new Date(now.getTime() - days * 86400_000).toISOString(), to: null };
  return { from: null, to: null };
}

function dayOf(ts: string): string {
  return ts.slice(0, 10);
}

/** "Thu 15 Oct" — log group and chart labels (design/screens/Logs.dc.html). */
function dayLabel(isoDate: string, todayIso: string): string {
  const label = new Date(`${isoDate}T00:00:00Z`).toLocaleDateString('en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    timeZone: 'UTC',
  });
  return isoDate === todayIso ? `${label} · today` : label;
}

const mono = { fontFamily: 'var(--sa-font-mono)' } as const;

function LevelChip({ level }: { level: string }) {
  return (
    <Chip variant={LEVEL_CHIP[level] ?? 'neutral'}>{level.toLowerCase()}</Chip>
  );
}

function LogRow({
  line,
  expanded,
  onToggle,
}: {
  line: LogLine;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      className="sa-row"
      aria-expanded={expanded}
      onClick={onToggle}
      style={{
        width: '100%',
        display: 'grid',
        gridTemplateColumns: '112px 84px minmax(0, 1fr)',
        gap: '6px var(--sa-space-3)',
        alignItems: 'start',
        padding: '10px var(--sa-space-3)',
        border: 0,
        borderRadius: 'var(--sa-radius-sm)',
        textAlign: 'left',
        background: expanded ? 'var(--sa-subtle)' : 'transparent',
        color: 'var(--sa-text)',
        fontSize: 'var(--sa-text-md)',
        cursor: 'pointer',
      }}
    >
      <span
        title={`New York ${new Date(line.ts).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'America/New_York' })}`}
        style={{ ...mono, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', paddingTop: 2 }}
      >
        {line.ts.slice(11, 23)}
      </span>
      <span>
        <LevelChip level={line.level} />
      </span>
      <span style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
        <span style={{ lineHeight: 1.45, overflowWrap: 'anywhere' }}>
          {line.message.split('\n')[0]}
        </span>
        <span style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
          <span style={{ ...mono, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
            {line.logger}
          </span>
          {[line.cycle, line.symbol, line.error_id]
            .filter((v): v is string => Boolean(v))
            .map((v) => (
              <span
                key={v}
                style={{
                  ...mono,
                  fontSize: 'var(--sa-text-xs)',
                  color: 'var(--sa-text-2)',
                  background: 'var(--sa-subtle)',
                  border: '1px solid var(--sa-border)',
                  borderRadius: 4,
                  padding: '1px 6px',
                  whiteSpace: 'nowrap',
                }}
              >
                {v}
              </span>
            ))}
        </span>
      </span>
    </button>
  );
}

/** Expanded line: error id and process, the exception, the stored JSON line,
 * and the surrounding lines (design/screens/Logs.dc.html). */
function ExpandedLine({ line, around }: { line: LogLine; around: LogLine[] }) {
  return (
    <div
      style={{
        margin: '4px 0 var(--sa-space-3) var(--sa-space-3)',
        padding: '14px var(--sa-space-4)',
        borderLeft: '3px solid var(--sa-negative)',
        background: 'var(--sa-panel)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sa-space-3)',
      }}
    >
      <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>
        {line.error_id && <>Error id <span style={mono}>{line.error_id}</span> · </>}
        process {line.process} · pid {line.pid}
      </div>
      {line.traceback && (
        <>
          <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>Exception</div>
          <pre style={{ margin: 0, padding: 'var(--sa-space-3)', background: 'var(--sa-bg)', border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-md)', ...mono, fontSize: 'var(--sa-text-sm)', lineHeight: 1.55, color: 'var(--sa-negative-soft)', whiteSpace: 'pre-wrap', overflowX: 'auto' }}>
            {line.traceback}
          </pre>
        </>
      )}
      <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>Stored line</div>
      <pre style={{ margin: 0, padding: 'var(--sa-space-3)', background: 'var(--sa-bg)', border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-md)', ...mono, fontSize: 'var(--sa-text-sm)', lineHeight: 1.55, color: 'var(--sa-text-2)', whiteSpace: 'pre-wrap', overflowX: 'auto' }}>
        {JSON.stringify(line, null, 2)}
      </pre>
      {around.length > 0 && (
        <>
          <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
            Surrounding lines · same index order
          </div>
          <div style={{ border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-md)', padding: 6, overflowX: 'auto' }}>
            <div style={{ minWidth: 560 }}>
              {around.map((a) => (
                <div
                  key={a.id}
                  style={{
                    display: 'grid',
                    gridTemplateColumns: '96px 76px 120px minmax(0, 1fr)',
                    gap: 8,
                    padding: '5px 8px',
                    borderRadius: 4,
                    ...mono,
                    fontSize: 'var(--sa-text-sm)',
                  }}
                >
                  <span style={{ color: 'var(--sa-text-muted)' }}>{a.ts.slice(11, 23)}</span>
                  <span style={{ color: LEVEL_COLOR[a.level] ?? 'var(--sa-text-2)' }}>{a.level}</span>
                  <span style={{ color: 'var(--sa-text-2)', overflow: 'hidden', textOverflow: 'ellipsis' }}>{a.logger}</span>
                  <span style={{ color: a.id === line.id ? 'var(--sa-text)' : 'var(--sa-text-2)', minWidth: 0, overflowWrap: 'anywhere' }}>
                    {a.message.split('\n')[0]}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
      {line.cycle && (
        <a href={`/logs?cycle=${line.cycle}`} style={{ color: 'var(--sa-accent)', fontSize: 'var(--sa-text-md)' }}>
          Related: cycle {line.cycle}
        </a>
      )}
    </div>
  );
}

/** "Review a recurring error" — the Expected/Fixed + required-note dialog
 * from design/screens/States.dc.html. */
function ReviewDialog({
  problem,
  onClose,
  onSaved,
}: {
  problem: Problem;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [kind, setKind] = useState<'expected' | 'fixed'>('fixed');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = () => {
    if (!note.trim()) return;
    setBusy(true);
    setError(null);
    post('/api/logs/review', {
      fingerprint: problem.fingerprint,
      review: kind,
      note: note.trim(),
      level: problem.level,
      sample: problem.sample,
    })
      .then(() => {
        setBusy(false);
        onSaved();
        onClose();
      })
      .catch((e: unknown) => {
        setBusy(false);
        setError(errorMessages(e)[0] ?? 'Save failed');
      });
  };

  const option = (value: 'expected' | 'fixed', title: string, text: string) => (
    <label
      style={{
        display: 'flex',
        gap: 'var(--sa-space-3)',
        padding: '14px',
        border: `1px solid ${kind === value ? 'var(--sa-accent)' : 'var(--sa-border)'}`,
        borderRadius: 'var(--sa-radius-md)',
        background: kind === value ? 'var(--sa-accent-bg)' : 'transparent',
        cursor: 'pointer',
      }}
    >
      <input
        type="radio"
        name="review-kind"
        checked={kind === value}
        onChange={() => setKind(value)}
        style={{ marginTop: 3 }}
      />
      <span>
        <b style={{ fontWeight: 600 }}>{title}</b>
        <br />
        <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>{text}</span>
      </span>
    </label>
  );

  return (
    <Modal
      open
      title="Review a recurring error"
      onClose={onClose}
      footer={
        <>
          <DialogButton variant="cancel" onClick={onClose}>Cancel</DialogButton>
          <DialogButton variant="accent" disabled={busy || !note.trim()} onClick={save}>
            Save review
          </DialogButton>
        </>
      }
    >
      <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.5 }}>
        <b style={{ color: 'var(--sa-text)' }}>{problem.sample}</b>
        <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)', marginTop: 4 }}>
          ×{problem.count} · last {displayDayTime(problem.last_ts)}
        </div>
      </div>
      {option(
        'expected',
        'Expected',
        'An outside failure the app handled as specified (for example an OpenAI outage). Stays reviewed if it recurs.',
      )}
      {option(
        'fixed',
        'Fixed',
        'The cause is solved. If it happens again, it counts as unreviewed for Readiness.',
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <label htmlFor="review-note" style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>
          Note (required, shown in Readiness history)
        </label>
        <textarea
          id="review-note"
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="e.g. new app password saved in Settings → Notifications, test email sent"
          style={{
            boxSizing: 'border-box',
            width: '100%',
            padding: 10,
            borderRadius: 'var(--sa-radius-md)',
            border: '1px solid var(--sa-border-strong)',
            background: 'var(--sa-bg)',
            color: 'var(--sa-text)',
            fontFamily: 'inherit',
            fontSize: 'var(--sa-text-base)',
            resize: 'vertical',
          }}
        />
      </div>
      {error && <div style={{ color: 'var(--sa-negative-text)', fontSize: 'var(--sa-text-sm)' }}>{error}</div>}
    </Modal>
  );
}

const FIELD: React.CSSProperties = {
  height: 40,
  boxSizing: 'border-box',
  borderRadius: 'var(--sa-radius-md)',
  border: '1px solid var(--sa-border-strong)',
  background: 'var(--sa-bg)',
  color: 'var(--sa-text)',
  fontSize: 'var(--sa-text-base)',
  padding: '0 10px',
};

export default function Logs() {
  const [params, setParams] = useSearchParams();
  const levels = useMemo(() => parseLevels(params.get('levels')), [params]);
  const [period, setPeriod] = useState('7 days');
  const [customFrom, setCustomFrom] = useState('');
  const [customTo, setCustomTo] = useState('');
  const [search, setSearch] = useState('');
  const [loggerFilter, setLoggerFilter] = useState('');
  const [symbolFilter, setSymbolFilter] = useState(params.get('symbol') ?? '');
  const [cycleFilter, setCycleFilter] = useState(params.get('cycle') ?? '');
  const [lines, setLines] = useState<LogLine[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [around, setAround] = useState<Record<number, LogLine[]>>({});
  const [follow, setFollow] = useState(true);
  const listRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reviewProblem, setReviewProblem] = useState<Problem | null>(null);

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
      const { from, to } = periodFrom(period, customFrom, customTo);
      if (from) q.set('from', from);
      if (to) q.set('to', to);
      if (search.trim()) q.set('search', search.trim());
      if (loggerFilter.trim()) q.set('logger', loggerFilter.trim());
      if (symbolFilter.trim()) q.set('symbol', symbolFilter.trim());
      if (cycleFilter.trim()) q.set('cycle', cycleFilter.trim());
      if (extra) for (const [k, v] of Object.entries(extra)) q.set(k, v);
      return q.toString();
    },
    [levels, period, customFrom, customTo, search, loggerFilter, symbolFilter, cycleFilter],
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
      const s = await get<Summary>(`/api/logs/summary`);
      setSummary(s);
      setLoadError(null);
    } catch (e: unknown) {
      setLoadError(errorMessages(e)[0] ?? 'the log could not be loaded');
    } finally {
      setLoading(false);
    }
  }, [buildQuery]);

  useEffect(() => {
    const t = setTimeout(() => void load(), 0);
    return () => clearTimeout(t);
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
      try {
        const data = await get<{ lines: LogLine[] }>(
          `/api/logs/${line.id}/around?window=10`,
        );
        setAround((prev) => ({ ...prev, [line.id]: data.lines }));
      } catch {
        // Surrounding lines are optional context; the expanded view still
        // shows the stored line without them.
      }
    }
  };

  const clearFilters = () => {
    setSearch('');
    setLoggerFilter('');
    setSymbolFilter('');
    setCycleFilter('');
    setPeriod('7 days');
    setCustomFrom('');
    setCustomTo('');
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

  const countByDay = useMemo(() => {
    const map = new Map<string, { warning: number; error: number }>();
    for (const [day, c] of dayCounts) map.set(day, c);
    return map;
  }, [dayCounts]);

  const todayIso = new Date().toISOString().slice(0, 10);
  const maxCount = Math.max(1, ...dayCounts.map(([, c]) => c.warning + c.error));
  const unreviewed = (summary?.problems ?? []).filter((p) => !p.review).length;

  return (
    <div style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-4)' }}>
      {/* Filters (design/screens/Logs.dc.html) */}
      <Card ariaLabel="Filters">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-3) var(--sa-space-5)', alignItems: 'flex-end' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
            <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
              Levels · warning and up by default
            </span>
            <div role="group" aria-label="Levels" style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
              {LEVELS.map((level) => {
                const on = levels.includes(level);
                const count = summary?.level_counts?.[level] ?? 0;
                return (
                  <button
                    key={level}
                    type="button"
                    aria-pressed={on}
                    onClick={() => toggleLevel(level)}
                    style={{
                      height: 36,
                      padding: '0 12px',
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 8,
                      borderRadius: 'var(--sa-radius-md)',
                      background: on ? 'var(--sa-subtle)' : 'transparent',
                      color: on ? 'var(--sa-text)' : 'var(--sa-text-muted)',
                      border: `1px ${on ? 'solid var(--sa-border-strong)' : 'dashed var(--sa-border)'}`,
                      fontSize: 'var(--sa-text-md)',
                      fontWeight: 600,
                      cursor: 'pointer',
                    }}
                  >
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: '50%',
                        background: on ? LEVEL_COLOR[level] : 'transparent',
                        border: `1px solid ${LEVEL_COLOR[level]}`,
                      }}
                    />
                    {level === 'CRITICAL' ? 'Critical' : level.charAt(0) + level.slice(1).toLowerCase()}
                    <span style={{ ...mono, fontWeight: 400 }}>
                      {count.toLocaleString('en-US')}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
          <Toggle
            label="Show debug and info"
            checked={showAll}
            onChange={toggleAll}
          />
          <span style={{ flex: 1 }} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
            <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>Period</span>
            <div role="group" aria-label="Period" style={{ display: 'inline-flex', flexWrap: 'wrap', alignSelf: 'flex-start', padding: 3, background: 'var(--sa-raised)', border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-md)' }}>
              {PERIODS.map((p) => (
                <button
                  key={p}
                  type="button"
                  aria-pressed={period === p}
                  onClick={() => setPeriod(p)}
                  style={{
                    height: 36,
                    padding: '0 12px',
                    border: 0,
                    borderRadius: 'var(--sa-radius-sm)',
                    background: period === p ? 'var(--sa-border-strong)' : 'transparent',
                    color: period === p ? 'var(--sa-text)' : 'var(--sa-text-muted)',
                    fontSize: 'var(--sa-text-md)',
                    fontWeight: 600,
                    whiteSpace: 'nowrap',
                    cursor: 'pointer',
                  }}
                >
                  {p}
                </button>
              ))}
            </div>
          </div>
          {period === 'Custom' && (
            <>
              <label style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                From
                <input type="date" value={customFrom} onChange={(e) => setCustomFrom(e.target.value)} style={FIELD} />
              </label>
              <label style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                To
                <input type="date" value={customTo} onChange={(e) => setCustomTo(e.target.value)} style={FIELD} />
              </label>
            </>
          )}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-3)', alignItems: 'flex-end' }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', flex: '2 1 240px', minWidth: 0 }}>
            Search messages
            <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="e.g. timeout, JPM, 503" aria-label="Search messages" style={{ ...FIELD, width: '100%' }} />
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', flex: '1 1 150px', minWidth: 0 }}>
            Component
            <input type="search" value={loggerFilter} onChange={(e) => setLoggerFilter(e.target.value)} placeholder="Any" aria-label="Component" style={{ ...FIELD, width: '100%' }} />
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', flex: '1 1 110px', minWidth: 0 }}>
            Symbol
            <input type="search" value={symbolFilter} onChange={(e) => setSymbolFilter(e.target.value)} placeholder="Any" aria-label="Symbol" style={{ ...FIELD, width: '100%' }} />
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', flex: '1 1 110px', minWidth: 0 }}>
            Cycle
            <input type="search" value={cycleFilter} onChange={(e) => setCycleFilter(e.target.value)} placeholder="e.g. c-0412" aria-label="Cycle" style={{ ...FIELD, width: '100%' }} />
          </label>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button type="button" onClick={clearFilters} style={{ height: 40, padding: '0 var(--sa-space-4)', borderRadius: 'var(--sa-radius-md)', border: '1px solid var(--sa-border)', background: 'transparent', color: 'var(--sa-text-2)', fontSize: 'var(--sa-text-base)', fontWeight: 600, cursor: 'pointer', whiteSpace: 'nowrap' }}>
              Clear filters
            </button>
            <a
              href={`/api/logs/export.jsonl?${buildQuery()}`}
              download="logs.jsonl"
              title="The filtered lines as JSON lines, as stored in the log file"
              style={{ height: 40, boxSizing: 'border-box', padding: '0 var(--sa-space-4)', display: 'inline-flex', alignItems: 'center', borderRadius: 'var(--sa-radius-md)', border: '1px solid var(--sa-border)', background: 'var(--sa-raised)', color: 'var(--sa-text)', fontSize: 'var(--sa-text-base)', fontWeight: 600, textDecoration: 'none', whiteSpace: 'nowrap' }}
            >
              Download .jsonl
            </a>
          </div>
        </div>
      </Card>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-5)', alignItems: 'flex-start' }}>
        {/* Warnings and errors per day — clicking a bar filters to that day. */}
        {dayCounts.length > 0 && (
          <Card label="Warnings and errors per day" right={period} style={{ flex: '1 1 360px' }} ariaLabel="Warnings and errors per day">
            <div role="group" aria-label="Warnings and errors per day" style={{ display: 'flex', gap: 6, alignItems: 'flex-end' }}>
              {dayCounts.map(([day, c]) => (
                <button
                  key={day}
                  type="button"
                  title={`${dayLabel(day, todayIso)}: ${c.warning} warnings · ${c.error} errors. Click to show this day.`}
                  onClick={() => {
                    setPeriod('Custom');
                    setCustomFrom(day);
                    setCustomTo(day);
                  }}
                  style={{ flex: '1 1 0', minWidth: 32, display: 'flex', flexDirection: 'column', gap: 6, background: 'transparent', border: 0, padding: 4, borderRadius: 'var(--sa-radius-sm)', cursor: 'pointer' }}
                >
                  <div style={{ height: 90, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', gap: 1 }}>
                    <div style={{ height: `${(c.error / maxCount) * 100}%`, minHeight: c.error ? 2 : 0, background: 'var(--sa-negative)' }} />
                    <div style={{ height: `${(c.warning / maxCount) * 100}%`, minHeight: c.warning ? 2 : 0, background: 'var(--sa-accent)' }} />
                  </div>
                  <span style={{ ...mono, fontSize: 'var(--sa-text-sm)', textAlign: 'center', color: 'var(--sa-text-muted)' }}>
                    {new Date(`${day}T00:00:00Z`).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', timeZone: 'UTC' })}
                  </span>
                </button>
              ))}
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px var(--sa-space-4)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><span style={{ width: 10, height: 10, borderRadius: 2, background: 'var(--sa-accent)' }} />Warning</span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><span style={{ width: 10, height: 10, borderRadius: 2, background: 'var(--sa-negative)' }} />Error and critical</span>
            </div>
            <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
              Debug and info are not counted here. Days without a session still show when lines were logged.
            </div>
          </Card>
        )}

        {/* Recurring problems (design: a table with a review column). */}
        {(summary?.problems.length ?? 0) > 0 && (
          <Card label="Recurring problems" right={`errors · ${unreviewed} unreviewed`} style={{ flex: '2 1 520px' }} ariaLabel="Recurring problems">
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', minWidth: 560 }}>
                <thead>
                  <tr>
                    {['Level', 'Problem', 'Count', 'First seen', 'Review'].map((h, i) => (
                      <th key={h} scope="col" style={{ textAlign: i === 2 ? 'right' : 'left', padding: '8px 10px', fontSize: 'var(--sa-text-sm)', fontWeight: 500, color: 'var(--sa-text-muted)', borderBottom: '1px solid var(--sa-border)', whiteSpace: 'nowrap' }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {summary!.problems.map((p) => (
                    <tr
                      key={p.fingerprint}
                      title="Filter the log on this problem"
                      className="sa-row"
                      role="button"
                      tabIndex={0}
                      onClick={() => setSearch(p.sample.split(':')[0])}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          setSearch(p.sample.split(':')[0]);
                        }
                      }}
                      style={{ cursor: 'pointer' }}
                    >
                      <td style={{ padding: 10, borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-md)', verticalAlign: 'top' }}>
                        <LevelChip level={p.level} />
                      </td>
                      <td style={{ padding: 10, borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-md)', verticalAlign: 'top' }}>
                        {p.sample}
                      </td>
                      <td style={{ padding: 10, borderBottom: '1px solid var(--sa-border)', ...mono, textAlign: 'right', verticalAlign: 'top' }}>
                        {p.count}
                      </td>
                      <td style={{ padding: 10, borderBottom: '1px solid var(--sa-border)', ...mono, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-2)', whiteSpace: 'nowrap', verticalAlign: 'top' }}>
                        {p.first_ts ? displayDayTime(p.first_ts) : '—'}
                        <br />
                        <span style={{ color: 'var(--sa-text-muted)' }}>last {displayDayTime(p.last_ts)}</span>
                      </td>
                      <td style={{ padding: 10, borderBottom: '1px solid var(--sa-border)', verticalAlign: 'top' }}>
                        {p.review ? (
                          <Chip variant="neutral">{p.review}</Chip>
                        ) : (
                          <span style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
                            <Chip variant="warning">unreviewed</Chip>
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                setReviewProblem(p);
                              }}
                              style={{ height: 32, padding: '0 10px', borderRadius: 'var(--sa-radius-sm)', border: '1px solid var(--sa-border-strong)', background: 'var(--sa-raised)', color: 'var(--sa-text)', fontSize: 'var(--sa-text-md)', fontWeight: 600, cursor: 'pointer' }}
                            >
                              Review
                            </button>
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.5 }}>
              Lines with the same component and event are grouped, with numbers and
              symbols ignored. Click a row to filter the log on it. Errors need a
              review (expected or fixed) for Readiness.
            </div>
          </Card>
        )}
      </div>

      {/* Log lines, grouped per day (design/screens/Logs.dc.html). */}
      <Card
        label="Log"
        right={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--sa-space-3)' }}>
            <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
              {lines.length} lines · newest first · times in Amsterdam, New York on hover
            </span>
            <button
              type="button"
              role="switch"
              aria-checked={follow}
              onClick={() => setFollow((f) => !f)}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 8,
                height: 32,
                padding: '0 10px',
                borderRadius: 'var(--sa-radius-sm)',
                border: `1px solid ${follow ? 'var(--sa-positive-border)' : 'var(--sa-border)'}`,
                background: follow ? 'var(--sa-positive-bg)' : 'transparent',
                color: follow ? 'var(--sa-positive)' : 'var(--sa-text-muted)',
                fontSize: 'var(--sa-text-md)',
                cursor: 'pointer',
              }}
            >
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: follow ? 'var(--sa-positive)' : 'var(--sa-text-muted)' }} />
              Follow new lines
            </button>
          </span>
        }
      >
        {loading && lines.length === 0 && <SkeletonLines bars={3} />}
        {loadError && (
          <FetchError
            what="the log"
            failure={{ at: new Date(), message: loadError }}
            updatedAt={null}
            onRetry={() => void load()}
          />
        )}
        {!loading && !loadError && lines.length === 0 && (
          <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
            No lines match the filters.
          </div>
        )}
        <div
          ref={listRef}
          onScroll={onScroll}
          role="log"
          aria-label="Log lines"
          style={{ maxHeight: '70vh', overflowY: 'auto' }}
          tabIndex={0}
        >
          {grouped.map(([day, dayLines]) => {
            const c = countByDay.get(day);
            return (
              <div key={day}>
                <div
                  style={{
                    position: 'sticky',
                    top: 0,
                    zIndex: 1,
                    display: 'flex',
                    justifyContent: 'space-between',
                    gap: 8,
                    padding: '10px 12px 6px',
                    background: 'var(--sa-panel)',
                    borderBottom: '1px solid var(--sa-border)',
                    fontSize: 'var(--sa-text-sm)',
                  }}
                >
                  <span style={{ fontWeight: 600, color: 'var(--sa-text-2)', textTransform: 'uppercase', letterSpacing: 'var(--sa-label-tracking)' }}>
                    {dayLabel(day, todayIso)}
                  </span>
                  {c && (
                    <span style={{ color: 'var(--sa-text-muted)' }}>
                      {c.warning} warnings · {c.error} errors
                    </span>
                  )}
                </div>
                {dayLines.map((line) => (
                  <div key={line.id}>
                    <LogRow line={line} expanded={expanded === line.id} onToggle={() => void openLine(line)} />
                    {expanded === line.id && (
                      <ExpandedLine line={line} around={around[line.id] ?? []} />
                    )}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
        {cursor !== null && (
          <button
            type="button"
            onClick={async () => {
              const data = await get<{ lines: LogLine[]; next_cursor: number | null }>(
                `/api/logs?${buildQuery({ cursor: String(cursor) })}&limit=200`,
              );
              setLines((prev) => [...prev, ...data.lines]);
              setCursor(data.next_cursor);
            }}
            style={{ alignSelf: 'center', height: 36, padding: '0 var(--sa-space-4)', borderRadius: 'var(--sa-radius-md)', border: '1px solid var(--sa-border)', background: 'var(--sa-raised)', color: 'var(--sa-text-2)', fontSize: 'var(--sa-text-md)', fontWeight: 600, cursor: 'pointer' }}
          >
            Load older
          </button>
        )}
      </Card>

      {reviewProblem && (
        <ReviewDialog
          problem={reviewProblem}
          onClose={() => setReviewProblem(null)}
          onSaved={() => void load()}
        />
      )}
    </div>
  );
}
