import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { Link, useLocation, useSearchParams } from 'react-router-dom';

import { createSequence } from '../api/latestWins';
import { ApiError, errorMessages } from '../api/client';
import {
  getLlmCall,
  getLlmCalls,
  getLlmCallsSummary,
  llmCallsExportUrl,
} from '../api/state';
import type { FetchFailure } from '../api/useResource';
import { useApiResource } from '../api/useResource';
import { useMediaQuery } from '../hooks/useMediaQuery';
import { Card } from '../components/Card';
import { CardBody, FetchError, SkeletonLines } from '../components/cardState';
import { RecordNotFound } from '../components/errorPages';
import {
  actionColor,
  contextLabel,
  formatCost,
  formatLatency,
} from '../components/decisionFormat';
import { displayTime, nyTime, sessionDayShort } from '../format';
import type {
  LlmCallDetail,
  LlmCallRow,
  LlmCallsPage,
  LlmCallsSummary,
  LlmCallSource,
} from '../types/api';
import {
  ACTION_OPTIONS,
  apiFilters,
  chipText,
  DEFAULT_SOURCES,
  groupBySession,
  moreFilterCount,
  OUTCOMES,
  parseFilters,
  PERIODS,
  REASON_OPTIONS,
  resolveRange,
  reviewTitle,
  rowContext,
  rowTitle,
  SOURCES,
  toSearchParams,
  type InspectorFilters,
  type Period,
} from './inspectorFilters';

const PAGE_SIZE = 50; // inspector.page_size
const PHONE = '(max-width: 640px)';

const OUTCOME_STYLE = {
  success: {
    color: 'var(--sa-positive)',
    background: 'var(--sa-positive-bg)',
    border: 'var(--sa-positive-border)',
  },
  warning: {
    color: 'var(--sa-warning-text)',
    background: 'var(--sa-warning-bg)',
    border: 'var(--sa-warning-border)',
  },
  error: {
    color: 'var(--sa-negative-text)',
    background: 'var(--sa-negative-bg)',
    border: 'var(--sa-negative-border)',
  },
} as const;

const INPUT: CSSProperties = {
  height: '40px',
  boxSizing: 'border-box',
  borderRadius: 'var(--sa-radius-md)',
  border: '1px solid var(--sa-border-strong)',
  background: 'var(--sa-bg)',
  color: 'var(--sa-text)',
  fontSize: 'var(--sa-text-base)',
  padding: '0 10px',
  width: '100%',
};

const FIELD_LABEL: CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 'var(--sa-space-1)',
  fontSize: 'var(--sa-text-sm)',
  color: 'var(--sa-text-muted)',
  flex: '1 1 150px',
  minWidth: 0,
};

const GROUP_LABEL: CSSProperties = {
  fontSize: 'var(--sa-text-sm)',
  color: 'var(--sa-text-muted)',
};

function Chip({ outcome, text }: { outcome: keyof typeof OUTCOME_STYLE; text: string }) {
  const style = OUTCOME_STYLE[outcome];
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        height: '22px',
        padding: '0 7px',
        borderRadius: 'var(--sa-radius-sm)',
        fontFamily: 'var(--sa-font-mono)',
        fontSize: 'var(--sa-text-xs)',
        whiteSpace: 'nowrap',
        color: style.color,
        background: style.background,
        border: `1px solid ${style.border}`,
      }}
    >
      {text}
    </span>
  );
}

/** One segmented group of toggle buttons — `aria-pressed` per PR 17. */
function ToggleGroup({
  label,
  options,
  pressed,
  onToggle,
}: {
  label: string;
  options: { value: string; label: ReactNode }[];
  pressed: (value: string) => boolean;
  onToggle: (value: string) => void;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', minWidth: 0 }}>
      <span style={GROUP_LABEL}>{label}</span>
      <div
        role="group"
        aria-label={label}
        style={{
          display: 'inline-flex',
          flexWrap: 'wrap',
          alignSelf: 'flex-start',
          padding: '3px',
          background: 'var(--sa-raised)',
          border: '1px solid var(--sa-border)',
          borderRadius: 'var(--sa-radius-md)',
        }}
      >
        {options.map((option) => {
          const on = pressed(option.value);
          return (
            <button
              key={option.value}
              type="button"
              aria-pressed={on}
              onClick={() => onToggle(option.value)}
              style={{
                height: '36px',
                padding: '0 var(--sa-space-3)',
                border: 0,
                borderRadius: 'var(--sa-radius-sm)',
                background: on ? 'var(--sa-border-strong)' : 'transparent',
                color: on ? 'var(--sa-text)' : 'var(--sa-text-muted)',
                fontSize: 'var(--sa-text-md)',
                fontWeight: 600,
                whiteSpace: 'nowrap',
                cursor: 'pointer',
              }}
            >
              {option.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

const GHOST_BUTTON: CSSProperties = {
  height: '40px',
  padding: '0 var(--sa-space-4)',
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 'var(--sa-space-2)',
  borderRadius: 'var(--sa-radius-md)',
  border: '1px solid var(--sa-border)',
  background: 'transparent',
  color: 'var(--sa-text-2)',
  fontSize: 'var(--sa-text-base)',
  fontWeight: 600,
  textDecoration: 'none',
  cursor: 'pointer',
  whiteSpace: 'nowrap',
  boxSizing: 'border-box',
};

const RAISED_BUTTON: CSSProperties = {
  ...GHOST_BUTTON,
  border: '1px solid var(--sa-border-strong)',
  background: 'var(--sa-raised)',
  color: 'var(--sa-text)',
};

function Filters({
  filters,
  summary,
  onUpdate,
}: {
  filters: InspectorFilters;
  summary: LlmCallsSummary | null;
  onUpdate: (patch: Partial<InspectorFilters>) => void;
}) {
  const phone = useMediaQuery(PHONE);
  const [moreOpen, setMoreOpen] = useState(false);
  const dates = summary?.session_dates ?? [];
  const range = resolveRange(filters, dates);
  const rangeText =
    range.from && range.to
      ? range.from === range.to
        ? sessionDayShort(range.from)
        : `${sessionDayShort(range.from)} – ${sessionDayShort(range.to)}`
      : 'all sessions';
  const moreCount = moreFilterCount(filters);

  const select = (
    key: 'action' | 'reason' | 'model' | 'prompt' | 'strategy',
    anyLabel: string,
    values: Record<string, number> | string[],
  ) => (
    <label style={FIELD_LABEL}>
      {key[0].toUpperCase() + key.slice(1)}
      <select
        style={INPUT}
        value={filters[key] ?? ''}
        onChange={(e) => onUpdate({ [key]: e.target.value || undefined })}
      >
        <option value="">{anyLabel}</option>
        {(Array.isArray(values) ? values : Object.keys(values)).map((v) => (
          <option key={v} value={v}>
            {Array.isArray(values) ? v : `${v} (${values[v]})`}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <section
      aria-label="Filters"
      style={{
        background: 'var(--sa-panel)',
        border: '1px solid var(--sa-border)',
        borderRadius: 'var(--sa-radius-lg)',
        padding: 'var(--sa-space-4) var(--sa-space-5)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sa-space-4)',
      }}
    >
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 'var(--sa-space-4) var(--sa-space-6)',
          alignItems: 'flex-end',
        }}
      >
        <ToggleGroup
          label="Period"
          options={PERIODS.map((p) => ({ value: p.value, label: p.label }))}
          pressed={(v) => filters.period === v}
          onToggle={(v) => {
            const period = v as Period;
            if (period === 'custom') {
              // Seed the inputs with the current effective range.
              const { from, to } = resolveRange(filters, dates);
              onUpdate({
                period,
                customFrom: filters.customFrom ?? from ?? dates[0],
                customTo: filters.customTo ?? to ?? dates[0],
              });
            } else {
              onUpdate({ period });
            }
          }}
        />
        {filters.period === 'custom' && (
          <div style={{ display: 'flex', gap: 'var(--sa-space-2)', alignItems: 'flex-end' }}>
            <label style={FIELD_LABEL}>
              From
              <input
                type="date"
                style={INPUT}
                value={filters.customFrom ?? ''}
                onChange={(e) =>
                  onUpdate({ customFrom: e.target.value || undefined })
                }
              />
            </label>
            <label style={FIELD_LABEL}>
              To
              <input
                type="date"
                style={INPUT}
                value={filters.customTo ?? ''}
                onChange={(e) =>
                  onUpdate({ customTo: e.target.value || undefined })
                }
              />
            </label>
          </div>
        )}
        <span style={{ flex: 1 }} />
        <span
          style={{
            fontSize: 'var(--sa-text-sm)',
            color: 'var(--sa-text-muted)',
            alignSelf: 'center',
          }}
        >
          New York session dates · {rangeText}
        </span>
      </div>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 'var(--sa-space-4) var(--sa-space-6)',
          alignItems: 'flex-end',
        }}
      >
        <ToggleGroup
          label="Outcome"
          options={[
            {
              value: '',
              label: (
                <>
                  All{' '}
                  <span style={{ fontFamily: 'var(--sa-font-mono)', fontWeight: 400 }}>
                    {summary?.total ?? ''}
                  </span>
                </>
              ),
            },
            ...OUTCOMES.map((o) => ({
              value: o as string,
              label: (
                <>
                  {o[0].toUpperCase() + o.slice(1)}{' '}
                  <span style={{ fontFamily: 'var(--sa-font-mono)', fontWeight: 400 }}>
                    {summary?.options.outcome[o] ?? 0}
                  </span>
                </>
              ),
            })),
          ]}
          pressed={(v) => (filters.outcome ?? '') === v}
          onToggle={(v) => onUpdate({ outcome: v || undefined })}
        />
        <ToggleGroup
          label="Source · pick one or more"
          options={SOURCES.map((s) => ({
            value: s.value,
            label: (
              <>
                {s.label}{' '}
                <span style={{ fontFamily: 'var(--sa-font-mono)', fontWeight: 400 }}>
                  {summary?.options.source[s.value] ?? 0}
                </span>
              </>
            ),
          }))}
          pressed={(v) => filters.sources.includes(v as LlmCallSource)}
          onToggle={(v) => {
            const source = v as LlmCallSource;
            const sources = filters.sources.includes(source)
              ? filters.sources.filter((s) => s !== source)
              : [...filters.sources, source];
            if (sources.length === 0) return; // one source stays on
            onUpdate({ sources });
          }}
        />
      </div>
      {phone && (
        <div style={{ display: 'flex', gap: 'var(--sa-space-2)', alignItems: 'center', flexWrap: 'wrap' }}>
          <button
            type="button"
            aria-expanded={moreOpen}
            onClick={() => setMoreOpen((v) => !v)}
            style={RAISED_BUTTON}
          >
            More filters
          </button>
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
            symbol, action, reason, model, prompt
            {moreCount === 0 ? ' · none set' : ` · ${moreCount} set`}
          </span>
        </div>
      )}
      {(!phone || moreOpen) && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-3)', alignItems: 'flex-end' }}>
          <label style={FIELD_LABEL}>
            Symbol
            <input
              type="search"
              placeholder="Any symbol"
              style={INPUT}
              value={filters.symbol ?? ''}
              onChange={(e) => onUpdate({ symbol: e.target.value || undefined })}
            />
          </label>
          {select('action', 'Any action', ACTION_OPTIONS)}
          {select('reason', 'Any reason', REASON_OPTIONS)}
          {select('model', 'Any model', summary?.options.model ?? [])}
          {select('prompt', 'Any prompt', summary?.options.prompt ?? [])}
          {filters.sources.includes('shadow') &&
            select('strategy', 'Any strategy', summary?.options.strategy ?? [])}
          <div style={{ display: 'flex', gap: 'var(--sa-space-2)', flexWrap: 'wrap' }}>
            <button
              type="button"
              onClick={() =>
                onUpdate({
                  period: 'last',
                  customFrom: undefined,
                  customTo: undefined,
                  outcome: undefined,
                  sources: [...DEFAULT_SOURCES],
                  symbol: undefined,
                  action: undefined,
                  reason: undefined,
                  model: undefined,
                  prompt: undefined,
                  strategy: undefined,
                  cycle: undefined,
                })
              }
              style={GHOST_BUTTON}
            >
              Clear filters
            </button>
            <a
              href={llmCallsExportUrl(apiFilters(filters, dates))}
              download
              title={`Metadata of the ${summary?.total ?? '—'} filtered calls: time, source, symbol, action, outcome, reasons, model, prompt, tokens, cost, latency. No prompts or responses.`}
              style={RAISED_BUTTON}
            >
              Export CSV
            </a>
          </div>
        </div>
      )}
    </section>
  );
}

function SummarySection({
  summary,
  onPickDay,
}: {
  summary: LlmCallsSummary;
  onPickDay: (day: string) => void;
}) {
  const share = (n: number) =>
    summary.total ? `${Math.round((n / summary.total) * 100)}%` : '—';
  const maxCalls = Math.max(1, ...summary.buckets.map((b) => b.calls));
  const stats: { label: string; value: ReactNode; sub: string }[] = [
    { label: 'Calls', value: summary.total, sub: `${summary.buckets.length} sessions` },
    {
      label: 'Success',
      value: <span style={{ color: 'var(--sa-positive)' }}>{summary.outcomes.success}</span>,
      sub: share(summary.outcomes.success),
    },
    {
      label: 'Warning',
      value: <span style={{ color: 'var(--sa-warning-text)' }}>{summary.outcomes.warning}</span>,
      sub: share(summary.outcomes.warning),
    },
    {
      label: 'Error',
      value: <span style={{ color: 'var(--sa-negative-text)' }}>{summary.outcomes.error}</span>,
      sub: share(summary.outcomes.error),
    },
    {
      label: 'Cost',
      value: formatCost(summary.cost_total !== null ? String(summary.cost_total) : null),
      sub: SOURCES.map((s) =>
        summary.cost_by_source[s.value] !== null &&
        summary.cost_by_source[s.value] !== undefined
          ? `${s.value === 'live' ? 'decisions' : s.value === 'shadow' ? 'shadows' : 'reviews'} $${summary.cost_by_source[s.value]!.toFixed(4)}`
          : null,
      )
        .filter(Boolean)
        .join(' · '),
    },
    {
      label: 'Latency',
      value: formatLatency(summary.latency.median_ms),
      sub: `median · p95 ${formatLatency(summary.latency.p95_ms)}`,
    },
  ];
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-5) var(--sa-space-7)', alignItems: 'flex-end' }}>
      <div style={{ flex: '2 1 420px', display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-4) var(--sa-space-5)' }}>
        {stats.map((stat) => (
          <div key={stat.label} style={{ flex: '1 1 120px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{stat.label}</span>
            <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: '20px', color: 'var(--sa-text)' }}>
              {stat.value}
            </span>
            <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{stat.sub}</span>
          </div>
        ))}
      </div>
      <div style={{ flex: '1 1 300px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-2)' }}>
        <div style={{ display: 'flex', gap: '6px', alignItems: 'flex-end' }} role="group" aria-label="Calls per session">
          {[...summary.buckets].reverse().map((bucket) => {
            const scale = 96 / maxCalls;
            return (
              <button
                key={bucket.session_date}
                type="button"
                title={`${sessionDayShort(bucket.session_date)}: ${bucket.calls} calls · ${bucket.success} success · ${bucket.warning} warning · ${bucket.error} error. Click to show this session.`}
                onClick={() => onPickDay(bucket.session_date)}
                style={{
                  flex: '1 1 0',
                  minWidth: '36px',
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'stretch',
                  gap: '6px',
                  background: 'transparent',
                  border: 0,
                  padding: '4px',
                  borderRadius: 'var(--sa-radius-sm)',
                  cursor: 'pointer',
                  color: 'var(--sa-text-2)',
                }}
              >
                <span style={{ height: '96px', display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', gap: '1px' }}>
                  {bucket.error > 0 && (
                    <span style={{ height: `${bucket.error * scale}px`, background: 'var(--sa-negative)' }} />
                  )}
                  {bucket.warning > 0 && (
                    <span style={{ height: `${bucket.warning * scale}px`, background: 'var(--sa-accent)' }} />
                  )}
                  {bucket.success > 0 && (
                    <span style={{ height: `${bucket.success * scale}px`, background: 'var(--sa-neutral-chip)' }} />
                  )}
                </span>
                <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', textAlign: 'center', color: 'var(--sa-text-muted)' }}>
                  {sessionDayShort(bucket.session_date).replace(/ \w+$/, '')}
                </span>
              </button>
            );
          })}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          {(
            [
              ['var(--sa-neutral-chip)', 'Success'],
              ['var(--sa-accent)', 'Warning'],
              ['var(--sa-negative)', 'Error'],
            ] as const
          ).map(([color, label]) => (
            <span key={label} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ width: '10px', height: '10px', borderRadius: '2px', background: color }} />
              {label}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

function CallListRow({
  call,
  selected,
  onSelect,
}: {
  call: LlmCallRow;
  selected: boolean;
  onSelect: () => void;
}) {
  const act = call.final_action ?? call.kind;
  return (
    <button
      type="button"
      aria-pressed={selected}
      aria-label={`${displayTime(call.sent_at)} ${rowTitle(call)} ${act}, ${call.outcome}`}
      onClick={onSelect}
      title={nyTime(call.sent_at)}
      className="sa-row"
      style={{
        display: 'grid',
        gridTemplateColumns: '44px minmax(0, 1fr) auto',
        gap: '10px',
        alignItems: 'center',
        width: '100%',
        minHeight: '52px',
        padding: '6px 10px',
        border: 0,
        borderRadius: 'var(--sa-radius-sm)',
        textAlign: 'left',
        fontSize: 'var(--sa-text-base)',
        color: 'var(--sa-text)',
        background: selected ? 'var(--sa-accent-bg)' : 'transparent',
        boxShadow: selected ? 'inset 3px 0 0 var(--sa-accent)' : 'none',
        fontFamily: 'inherit',
      }}
    >
      <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
        {displayTime(call.sent_at)}
      </span>
      <span style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: '2px' }}>
        <span style={{ display: 'flex', gap: 'var(--sa-space-2)', alignItems: 'baseline', minWidth: 0 }}>
          <span style={{ fontWeight: 600 }}>{rowTitle(call)}</span>
          <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: actionColor(act) }}>
            {act.replace(/_/g, ' ')}
          </span>
        </span>
        <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {rowContext(call)} · {call.model ?? '—'}
        </span>
      </span>
      <Chip outcome={call.outcome} text={chipText(call)} />
    </button>
  );
}

function CallDetails({
  detail,
  onCopy,
  copied,
  onBack,
}: {
  detail: LlmCallDetail;
  onCopy: () => void;
  copied: boolean;
  onBack: (() => void) | null;
}) {
  const location = useLocation();
  const style = OUTCOME_STYLE[detail.outcome];
  const sourceLabel =
    detail.source === 'live'
      ? 'Live decision'
      : detail.source === 'shadow'
        ? `Shadow · ${detail.strategy ?? '—'}`
        : 'Review';
  const price = (() => {
    try {
      const snap = JSON.parse(detail.prompt.user ?? 'null') as { price?: number } | null;
      return typeof snap?.price === 'number' ? snap.price.toFixed(2) : null;
    } catch {
      return null;
    }
  })();
  const title =
    detail.source === 'review'
      ? `${reviewTitle(detail.kind)} · ${sessionDayShort(detail.session_date)} ${displayTime(detail.sent_at)}`
      : `${rowTitle(detail)} · ${sessionDayShort(detail.session_date)} · ${displayTime(detail.sent_at)} cycle`;
  const errorLine = [
    detail.error.type,
    detail.error.http_status !== null ? `HTTP ${detail.error.http_status}` : null,
    detail.error.code,
    detail.error.message ? `"${detail.error.message}"` : null,
    detail.error.request_id ? `request id ${detail.error.request_id}` : null,
  ]
    .filter(Boolean)
    .join(' · ');

  const stats: { label: string; value: ReactNode }[] = [
    { label: 'Source', value: sourceLabel },
    {
      label: 'Model',
      value: `${detail.model ?? '—'} · temp ${detail.temperature ?? '—'}`,
    },
    { label: 'Prompt', value: detail.prompt_version ?? '—' },
    {
      label: 'Tokens in / out',
      value:
        detail.tokens_in !== null && detail.tokens_out !== null
          ? `${detail.tokens_in.toLocaleString('en-US')} / ${detail.tokens_out.toLocaleString('en-US')}`
          : '— (no usage returned)',
    },
    {
      label: 'Cost',
      value: detail.cost !== null ? formatCost(String(detail.cost)) : 'not recorded',
    },
    {
      label: 'Latency',
      value:
        detail.latency_ms !== null ? formatLatency(detail.latency_ms) : 'not recorded',
    },
  ];

  return (
    <>
      <section
        aria-label="Call details"
        style={{
          background: 'var(--sa-panel)',
          border: '1px solid var(--sa-border)',
          borderRadius: 'var(--sa-radius-lg)',
          padding: 'var(--sa-space-4) var(--sa-space-5)',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--sa-space-4)',
        }}
      >
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: 'var(--sa-space-2) var(--sa-space-4)' }}>
          {onBack && (
            <button
              type="button"
              onClick={onBack}
              style={{ ...GHOST_BUTTON, height: '32px', fontSize: 'var(--sa-text-md)' }}
            >
              ← Back to list
            </button>
          )}
          <h2 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>{title}</h2>
          <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
            {detail.source === 'review'
              ? 'Review call'
              : `${contextLabel(detail.context ?? 'candidate')}${price ? ` · price ${price}` : ''}`}
          </span>
          <span style={{ flex: 1 }} />
          {detail.link && (
            <Link
              to={detail.link}
              state={
                detail.link.startsWith('/decisions/')
                  ? { backgroundLocation: location }
                  : undefined
              }
              style={{ fontSize: 'var(--sa-text-md)' }}
            >
              {detail.source === 'review'
                ? 'Open in Reviews'
                : detail.source === 'shadow'
                  ? 'Open strategy'
                  : 'Open decision'}
            </Link>
          )}
          <button
            type="button"
            title="Copies a link to this call (?call=)"
            onClick={onCopy}
            style={{
              height: '32px',
              padding: '0 10px',
              borderRadius: 'var(--sa-radius-sm)',
              border: '1px solid var(--sa-border)',
              background: 'transparent',
              color: 'var(--sa-text-2)',
              fontSize: 'var(--sa-text-md)',
              cursor: 'pointer',
            }}
          >
            {copied ? 'Copied' : 'Copy link'}
          </button>
        </div>
        <div
          style={{
            padding: '12px 14px',
            borderRadius: 'var(--sa-radius-md)',
            fontSize: 'var(--sa-text-md)',
            border: `1px solid ${style.border}`,
            background: style.background,
            color: style.color,
          }}
        >
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-2)', alignItems: 'center' }}>
            <Chip outcome={detail.outcome} text={detail.outcome} />
            <b style={{ fontWeight: 600 }}>{detail.result}</b>
            {detail.reasons.length > 0 && (
              <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                {detail.reasons.join(' · ')}
              </span>
            )}
          </div>
          {(detail.explanation ?? (detail.outcome === 'success' ? 'No warnings.' : null)) && (
            <div style={{ color: 'var(--sa-text-2)', marginTop: '6px' }}>
              {detail.explanation ?? 'No warnings.'}
            </div>
          )}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: '10px' }}>
          {stats.map((stat) => (
            <div key={stat.label} style={{ padding: '10px', border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-md)', minWidth: 0 }}>
              <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{stat.label}</div>
              <div style={{ fontFamily: 'var(--sa-font-mono)', marginTop: '4px', overflowWrap: 'anywhere' }}>{stat.value}</div>
            </div>
          ))}
        </div>
      </section>
      {detail.outcome === 'error' && (
        <section
          aria-label="Error"
          style={{
            background: 'var(--sa-panel)',
            border: '1px solid var(--sa-negative-border)',
            borderRadius: 'var(--sa-radius-lg)',
            padding: 'var(--sa-space-4) var(--sa-space-5)',
            display: 'flex',
            flexDirection: 'column',
            gap: '10px',
          }}
        >
          <h3 style={{ margin: 0, fontSize: 'var(--sa-text-md)', fontWeight: 600, color: 'var(--sa-text-2)', textTransform: 'uppercase', letterSpacing: 'var(--sa-label-tracking)' }}>
            Error
          </h3>
          <MonoPre>{errorLine || '—'}</MonoPre>
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
            The request was sent but no usable response came back, so there is
            no raw response. Keys and headers are never stored.
          </span>
        </section>
      )}
      <section
        aria-label="Prompt"
        style={{
          background: 'var(--sa-panel)',
          border: '1px solid var(--sa-border)',
          borderRadius: 'var(--sa-radius-lg)',
          padding: 'var(--sa-space-4) var(--sa-space-5)',
          display: 'flex',
          flexDirection: 'column',
          gap: '10px',
        }}
      >
        <h3 style={{ margin: 0, fontSize: 'var(--sa-text-md)', fontWeight: 600, color: 'var(--sa-text-2)', textTransform: 'uppercase', letterSpacing: 'var(--sa-label-tracking)' }}>
          Prompt
        </h3>
        <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          System · {detail.prompt_version ?? '—'} ·{' '}
          {detail.prompt.mismatch
            ? 'hash differs from the prompt file — the text is withheld'
            : 'hash matches the prompt file'}
        </div>
        {detail.prompt.system !== null && <MonoPre>{detail.prompt.system}</MonoPre>}
        <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          {detail.source === 'review' ? 'User · facts computed by code' : 'User · input snapshot'}
        </div>
        <MonoPre>{detail.prompt.user ?? '—'}</MonoPre>
      </section>
      {detail.raw_response !== null && (
        <section
          aria-label="Raw response"
          style={{
            background: 'var(--sa-panel)',
            border: '1px solid var(--sa-border)',
            borderRadius: 'var(--sa-radius-lg)',
            padding: 'var(--sa-space-4) var(--sa-space-5)',
            display: 'flex',
            flexDirection: 'column',
            gap: '10px',
          }}
        >
          <h3 style={{ margin: 0, fontSize: 'var(--sa-text-md)', fontWeight: 600, color: 'var(--sa-text-2)', textTransform: 'uppercase', letterSpacing: 'var(--sa-label-tracking)' }}>
            Raw response
          </h3>
          <MonoPre>{detail.raw_response}</MonoPre>
        </section>
      )}
    </>
  );
}

function MonoPre({ children }: { children: string }) {
  return (
    <pre
      style={{
        margin: 0,
        padding: 'var(--sa-space-3)',
        background: 'var(--sa-bg)',
        border: '1px solid var(--sa-border)',
        borderRadius: 'var(--sa-radius-md)',
        fontFamily: 'var(--sa-font-mono)',
        fontSize: 'var(--sa-text-sm)',
        lineHeight: 1.55,
        color: 'var(--sa-text-2)',
        whiteSpace: 'pre-wrap',
        overflowX: 'auto',
      }}
    >
      {children}
    </pre>
  );
}

interface PageState {
  calls: LlmCallRow[];
  total: number;
  cursor: string | null;
  hasMore: boolean;
}

export default function Inspector() {
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = parseFilters(searchParams);
  const phone = useMediaQuery(PHONE);
  const [dates, setDates] = useState<string[] | null>(null);
  const effective = apiFilters(filters, dates ?? []);
  const effectiveSig = JSON.stringify(effective);
  const rangeReady =
    filters.period === 'all' || filters.period === 'custom' || dates !== null;

  const update = useCallback(
    (patch: Partial<InspectorFilters>) => {
      setSearchParams((prev) => {
        const current = parseFilters(prev);
        return toSearchParams({ ...current, ...patch });
      });
    },
    [setSearchParams],
  );

  // The summary is fetched with whatever range is resolvable now; the
  // session_dates it returns (date filter skipped) resolve the period
  // presets and trigger a refetch — the probe response is never shown.
  const summary = useApiResource<LlmCallsSummary>(
    () => getLlmCallsSummary(effective),
    [effectiveSig],
  );
  const [prevSummaryData, setPrevSummaryData] = useState(summary.data);
  if (summary.data !== prevSummaryData) {
    setPrevSummaryData(summary.data);
    if (summary.data) setDates(summary.data.session_dates);
  }

  // The list pages accumulate under a latest-wins sequence: a filter change
  // or a new page supersedes anything still in flight.
  const [page, setPage] = useState<PageState>({ calls: [], total: 0, cursor: null, hasMore: false });
  const [listLoading, setListLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [listFailure, setListFailure] = useState<FetchFailure | null>(null);
  const [listRetry, setListRetry] = useState(0);
  const listSeq = useRef(createSequence());
  const effectiveRef = useRef(effective);
  useEffect(() => {
    effectiveRef.current = effective;
  });

  const fetchKey = `${effectiveSig}${filters.order}${rangeReady}${listRetry}`;
  const [prevFetchKey, setPrevFetchKey] = useState(fetchKey);
  if (fetchKey !== prevFetchKey) {
    setPrevFetchKey(fetchKey);
    if (rangeReady) {
      setListLoading(true);
      setListFailure(null);
    }
  }

  useEffect(() => {
    if (!rangeReady) return;
    const isCurrent = listSeq.current.next();
    getLlmCalls(effectiveRef.current, undefined, PAGE_SIZE, filters.order)
      .then((result: LlmCallsPage) => {
        if (!isCurrent()) return;
        setPage({
          calls: result.calls,
          total: result.total,
          cursor: result.next_cursor,
          hasMore: result.has_more,
        });
        setListLoading(false);
      })
      .catch((e: unknown) => {
        if (!isCurrent()) return;
        setListLoading(false);
        setListFailure({
          at: new Date(),
          message: errorMessages(e)[0] ?? 'request failed',
          unreachable: !(e instanceof ApiError),
        });
      });
  }, [effectiveSig, filters.order, rangeReady, listRetry]);

  const loadMore = useCallback(() => {
    const cursor = page.cursor;
    if (!cursor) return;
    const isCurrent = listSeq.current.next();
    setLoadingMore(true);
    getLlmCalls(effectiveRef.current, cursor, PAGE_SIZE, filters.order)
      .then((result: LlmCallsPage) => {
        if (!isCurrent()) return;
        setPage((prev) => ({
          calls: [...prev.calls, ...result.calls],
          total: result.total,
          cursor: result.next_cursor,
          hasMore: result.has_more,
        }));
        setLoadingMore(false);
      })
      .catch((e: unknown) => {
        if (!isCurrent()) return;
        setLoadingMore(false);
        setListFailure({
          at: new Date(),
          message: errorMessages(e)[0] ?? 'request failed',
          unreachable: !(e instanceof ApiError),
        });
      });
  }, [page.cursor, filters.order]);

  const detail = useApiResource<LlmCallDetail>(
    () => getLlmCall(filters.call!),
    [filters.call],
    filters.call !== undefined,
  );

  const [copied, setCopied] = useState(false);
  const copyLink = useCallback(() => {
    void navigator.clipboard?.writeText(window.location.href).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    });
  }, []);

  const sessionGroups = groupBySession(page.calls);
  const lastDate = dates?.[0];

  return (
    <div
      style={{
        maxWidth: 'var(--sa-page-max)',
        margin: '0 auto',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sa-space-5)',
      }}
    >
      <Filters filters={filters} summary={summary.data ?? null} onUpdate={update} />

      <section
        aria-label="Filtered calls"
        style={{
          background: 'var(--sa-panel)',
          border: '1px solid var(--sa-border)',
          borderRadius: 'var(--sa-radius-lg)',
          padding: 'var(--sa-space-4) var(--sa-space-5)',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--sa-space-4)',
          minWidth: 0,
          boxSizing: 'border-box',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 'var(--sa-space-3)', flexWrap: 'wrap' }}>
          <h2 style={{ margin: 0, fontSize: 'var(--sa-text-md)', fontWeight: 600, color: 'var(--sa-text-2)', textTransform: 'uppercase', letterSpacing: 'var(--sa-label-tracking)' }}>
            Filtered calls
          </h2>
          <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>totals follow the filters</div>
        </div>
        {rangeReady && summary.data ? (
          <>
            {summary.failure && (
              <FetchError
                what="the summary"
                failure={summary.failure}
                updatedAt={summary.updatedAt}
                onRetry={summary.reload}
              />
            )}
            <SummarySection
              summary={summary.data}
              onPickDay={(day) =>
                update({ period: 'custom', customFrom: day, customTo: day })
              }
            />
          </>
        ) : summary.failure ? (
          <FetchError
            what="the summary"
            failure={summary.failure}
            updatedAt={null}
            onRetry={summary.reload}
          />
        ) : (
          <SkeletonLines bars={4} />
        )}
      </section>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-5)', alignItems: 'flex-start' }}>
        <section
          aria-label="Model calls"
          style={{
            flex: '1 1 340px',
            maxWidth: phone ? '100%' : '440px',
            minWidth: 0,
            background: 'var(--sa-panel)',
            border: '1px solid var(--sa-border)',
            borderRadius: 'var(--sa-radius-lg)',
            display: 'flex',
            flexDirection: 'column',
            boxSizing: 'border-box',
            overflow: 'hidden',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 'var(--sa-space-2)', padding: '14px 16px 8px' }}>
            <h2 style={{ margin: 0, fontSize: 'var(--sa-text-md)', fontWeight: 600, color: 'var(--sa-text-2)', textTransform: 'uppercase', letterSpacing: 'var(--sa-label-tracking)' }}>
              Calls
            </h2>
            <button
              type="button"
              aria-pressed={filters.order === 'asc'}
              title="Switch between newest and oldest first"
              onClick={() => update({ order: filters.order === 'asc' ? 'desc' : 'asc' })}
              style={{
                border: 0,
                background: 'transparent',
                padding: 0,
                fontSize: 'var(--sa-text-sm)',
                color: 'var(--sa-text-muted)',
                cursor: 'pointer',
                fontFamily: 'inherit',
              }}
            >
              {filters.order === 'asc' ? 'oldest first' : 'newest first'}
            </button>
          </div>
          <div style={{ maxHeight: '1060px', overflowY: 'auto', padding: '0 8px 8px' }} tabIndex={0} aria-label="Call list, scrollable">
            {listLoading ? (
              <div style={{ padding: 'var(--sa-space-4)' }}>
                <SkeletonLines bars={6} />
              </div>
            ) : listFailure && page.calls.length === 0 ? (
              <div style={{ padding: 'var(--sa-space-4)' }}>
                <FetchError
                  what="the calls"
                  failure={listFailure}
                  updatedAt={null}
                  onRetry={() => setListRetry((n) => n + 1)}
                />
              </div>
            ) : page.calls.length === 0 ? (
              <div style={{ padding: 'var(--sa-space-4)', color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
                No calls match the filters.
              </div>
            ) : (
              sessionGroups.map((group) => {
                const warnings = group.calls.filter((c) => c.outcome === 'warning').length;
                const errors = group.calls.filter((c) => c.outcome === 'error').length;
                const counts = [
                  `${group.calls.length} calls`,
                  warnings ? `${warnings} warnings` : null,
                  errors ? `${errors} errors` : null,
                ]
                  .filter(Boolean)
                  .join(' · ');
                return (
                  <div key={group.date}>
                    <div
                      style={{
                        position: 'sticky',
                        top: 0,
                        zIndex: 1,
                        display: 'flex',
                        justifyContent: 'space-between',
                        gap: 'var(--sa-space-2)',
                        padding: '10px 8px 6px',
                        background: 'var(--sa-panel)',
                        borderBottom: '1px solid var(--sa-border)',
                        fontSize: 'var(--sa-text-sm)',
                      }}
                    >
                      <span style={{ fontWeight: 600, color: 'var(--sa-text-2)', textTransform: 'uppercase', letterSpacing: 'var(--sa-label-tracking)' }}>
                        {sessionDayShort(group.date)}
                        {group.date === lastDate ? ' · last session' : ''}
                      </span>
                      <span style={{ color: 'var(--sa-text-muted)' }}>{counts}</span>
                    </div>
                    {group.calls.map((call) => (
                      <CallListRow
                        key={call.id}
                        call={call}
                        selected={call.id === filters.call}
                        onSelect={() => update({ call: call.id })}
                      />
                    ))}
                  </div>
                );
              })
            )}
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 'var(--sa-space-2)', padding: '10px 16px', borderTop: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
            <span>
              {page.calls.length} of {page.total} loaded
            </span>
            {page.hasMore && (
              <button
                type="button"
                onClick={loadMore}
                disabled={loadingMore}
                style={{ ...GHOST_BUTTON, height: '36px', fontSize: 'var(--sa-text-base)' }}
              >
                {loadingMore
                  ? 'Loading…'
                  : `Load ${Math.min(PAGE_SIZE, page.total - page.calls.length)} more`}
              </button>
            )}
          </div>
        </section>

        <div style={{ flex: '999 1 560px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-4)' }}>
          {filters.call === undefined ? (
            <Card label="Call details">
              <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
                Select a call to see the prompt, the input snapshot and the raw
                response.
              </div>
            </Card>
          ) : detail.failure?.status === 404 ? (
            <RecordNotFound
              what={`Model call ${filters.call} doesn't exist`}
              why="Calls are never deleted within a run."
              searchTo="/inspector"
              searchLabel="Search the LLM inspector"
            />
          ) : (
            <CardBody
              res={detail}
              what="the call"
              skeleton={<SkeletonLines bars={5} />}
            >
              {(d) => (
                <CallDetails
                  detail={d}
                  onCopy={copyLink}
                  copied={copied}
                  onBack={phone ? () => update({ call: undefined }) : null}
                />
              )}
            </CardBody>
          )}
        </div>
      </div>
    </div>
  );
}
