import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
import { CardBoundary } from '../components/CardBoundary';
import { CardBody, FetchError, SkeletonLines } from '../components/cardState';
import { SkeletonList } from '../components/skeletons';
import { RecordNotFound } from '../components/errorPages';
import {
  actionColor,
  contextLabel,
  formatCost,
  formatLatency,
} from '../components/decisionFormat';
import { displayTime, nyTime, sessionDayShort } from '../format';
import type {
  LlmCallAttention,
  LlmCallDetail,
  LlmCallRow,
  LlmCallsPage,
  LlmCallsSummary,
  LlmCallSource,
} from '../types/api';
import {
  ACTION_OPTIONS,
  apiFilters,
  ATTENTION_LEVELS,
  attentionLabel,
  chipText,
  DEFAULT_SOURCES,
  foldCalls,
  foldLabel,
  groupBySession,
  moreFilterCount,
  OUTCOMES,
  parseFilters,
  PERIODS,
  REASON_LABELS,
  REASON_OPTIONS,
  resolveRange,
  reviewTitle,
  rowContext,
  rowTitle,
  SOURCES,
  toSearchParams,
  type InspectorFilters,
  type ListItem,
  type Period,
} from './inspectorFilters';
import { ACTIONS, REASON_CODES, labelFor } from '../labels';

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

const ATTENTION_STYLE: Record<
  LlmCallAttention,
  { color: string; background: string; border: string }
> = {
  none: {
    color: '#3DD68C',
    background: '#123A27',
    border: '#1E5A3E',
  },
  watch: {
    color: '#FFD58A',
    background: '#3A2A0E',
    border: '#6B4E17',
  },
  act: {
    color: '#FF8A8F',
    background: '#3A1416',
    border: '#7A2A2E',
  },
};

/** The attention chip (item 074): its reason on hover. */
function AttentionChip({
  level,
  why,
}: {
  level: LlmCallAttention;
  why?: string;
}) {
  const style = ATTENTION_STYLE[level] ?? ATTENTION_STYLE.none;
  return (
    <span
      title={why}
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
      {attentionLabel(level)}
    </span>
  );
}

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
    label: (v: string) => string = (v) => v,
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
            {Array.isArray(values) ? label(v) : `${label(v)} (${values[v]})`}
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
      <CardBoundary name="Filters">
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
          label="Attention"
          options={[
            { value: '', label: 'All' },
            ...ATTENTION_LEVELS.map((l) => ({
              value: l.value,
              label: (
                <>
                  {l.label}{' '}
                  <span style={{ fontFamily: 'var(--sa-font-mono)', fontWeight: 400 }}>
                    {summary?.attention?.[l.value] ?? 0}
                  </span>
                </>
              ),
            })),
          ]}
          pressed={(v) => (filters.attention ?? '') === v}
          onToggle={(v) =>
            onUpdate({ attention: (v || undefined) as LlmCallAttention | undefined })
          }
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
          {select('action', 'Any action', ACTION_OPTIONS, (v) => labelFor(ACTIONS, v))}
          {select(
            'reason',
            'Any reason',
            [
              ...REASON_OPTIONS,
              // "changed_by_code · <reason>" composite options from the
              // summary's counts (docs/SPEC.md, LLM call history —
              // Filters). No-call reasons never appear — no call was sent.
              ...Object.keys(summary?.options.reason ?? {}).filter(
                (r) => r.startsWith('changed_by_code.') && !REASON_OPTIONS.includes(r),
              ),
            ],
            (v) =>
              v.startsWith('changed_by_code.')
                ? `Changed by code · ${labelFor(REASON_CODES, v.slice('changed_by_code.'.length))}`
                : REASON_LABELS[v] ?? labelFor(REASON_CODES, v),
          )}
          {select('model', 'Any model', summary?.options.model ?? [])}
          {select(
            'prompt',
            'Any prompt',
            // Every version and kind from the manifest — not only
            // prompts that have calls.
            summary?.prompt_manifest ?? Object.keys(summary?.options.prompt ?? {}),
          )}
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
      </CardBoundary>
    </section>
  );
}

function SummarySection({
  summary,
  onPickDay,
  onPickReason,
}: {
  summary: LlmCallsSummary;
  onPickDay: (day: string) => void;
  onPickReason: (filter: string) => void;
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
        {summary.attention && (
          <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
            {summary.attention.act} need you · {summary.attention.watch} to watch ·{' '}
            {summary.attention.none} nothing to do
          </div>
        )}
        {summary.top_reasons && summary.top_reasons.length > 0 && (
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: '4px',
              fontSize: 'var(--sa-text-sm)',
            }}
          >
            <span style={{ color: 'var(--sa-text-muted)' }}>Top reasons</span>
            {summary.top_reasons.map((row) => (
              <button
                key={row.filter}
                type="button"
                onClick={() => onPickReason(row.filter)}
                title={row.fix ?? undefined}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 'var(--sa-space-2)',
                  border: 0,
                  background: 'transparent',
                  padding: '2px 0',
                  cursor: 'pointer',
                  color: 'var(--sa-text-2)',
                  fontSize: 'var(--sa-text-sm)',
                  textAlign: 'left',
                }}
              >
                <span style={{ fontFamily: 'var(--sa-font-mono)' }}>
                  ×{row.count}
                </span>
                <span>
                  {row.change_reason
                    ? `Changed by code · ${labelFor(REASON_CODES, row.change_reason)}`
                    : (REASON_LABELS[row.reason] ??
                      labelFor(REASON_CODES, row.reason))}
                  {row.kind ? ` · ${row.kind}` : ''}
                </span>
                <AttentionChip level={row.attention} />
              </button>
            ))}
          </div>
        )}
        <div
          style={{ display: 'flex', gap: '6px', alignItems: 'flex-end', overflowX: 'auto' }}
          role="group"
          aria-label="Calls per session"
        >
          {[...summary.buckets].reverse().map((bucket) => {
            const scale = 96 / maxCalls;
            const dayLabel = bucket.label ?? sessionDayShort(bucket.session_date);
            return (
              <button
                key={bucket.key ?? bucket.session_date}
                type="button"
                title={`${dayLabel}: ${bucket.calls} calls · ${bucket.success} success · ${bucket.warning} warning · ${bucket.error} error. Click to show this session.`}
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
                  {dayLabel.replace(/ \w+$/, '')}
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
      aria-label={`${displayTime(call.sent_at)} ${rowTitle(call)} ${labelFor(ACTIONS, act)}, ${call.outcome}`}
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
            {labelFor(ACTIONS, act)}
          </span>
        </span>
        <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {rowContext(call)} · {call.model ?? '—'}{call.provider ? ` · ${call.provider}` : ''}
        </span>
      </span>
      <Chip outcome={call.outcome} text={chipText(call)} />
    </button>
  );
}

/** A folded run of identical calls — opens in place to the member rows. */
function FoldRow({
  item,
  selectedId,
  onSelect,
}: {
  item: Extract<ListItem, { type: 'fold' }>;
  selectedId?: number;
  onSelect: (id: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const first = item.calls[0];
  const act = first.final_action ?? first.kind;
  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
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
          background: 'transparent',
          fontFamily: 'inherit',
          cursor: 'pointer',
        }}
      >
        <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          {displayTime(first.sent_at)}
        </span>
        <span style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: '2px' }}>
          <span style={{ display: 'flex', gap: 'var(--sa-space-2)', alignItems: 'baseline', minWidth: 0 }}>
            <span style={{ fontWeight: 600 }}>{foldLabel(item)}</span>
            <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: actionColor(act) }}>
              {labelFor(ACTIONS, act)}
            </span>
          </span>
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {rowContext(first)} · {first.model ?? 'not recorded'}
            {first.provider ? ` · ${first.provider}` : ''}
          </span>
        </span>
        <span style={{ display: 'inline-flex', gap: 'var(--sa-space-2)', alignItems: 'center' }}>
          <AttentionChip level={first.attention} />
          <Chip outcome={first.outcome} text={chipText(first)} />
        </span>
      </button>
      {open &&
        item.calls.map((call) => (
          <CallListRow
            key={call.id}
            call={call}
            selected={call.id === selectedId}
            onSelect={() => onSelect(call.id)}
          />
        ))}
    </div>
  );
}

function CallDetails({
  detail,
  onCopy,
  copied,
  onBack,
  onNewer,
  onOlder,
  canNewer,
  canOlder,
}: {
  detail: LlmCallDetail;
  onCopy: () => void;
  copied: boolean;
  onBack: (() => void) | null;
  onNewer: () => void;
  onOlder: () => void;
  canNewer: boolean;
  canOlder: boolean;
}) {
  const location = useLocation();
  const style = OUTCOME_STYLE[detail.outcome];
  const sourceLabel =
    detail.source === 'live'
      ? 'Live decision'
      : detail.source === 'shadow'
        ? `Shadow · ${detail.strategy ?? '—'}`
        : detail.source === 'ask'
          ? 'Ask the agent'
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
    detail.error.provider ? `${detail.error.provider} ` : null,
    detail.error.request_id ? `request id ${detail.error.request_id}` : null,
  ]
    .filter(Boolean)
    .join(' · ');

  const stats: { label: string; value: ReactNode }[] = [
    { label: 'Source', value: sourceLabel },
    {
      label: 'Model',
      value:
        detail.model === null
          ? 'not recorded'
          : `${detail.model}${detail.temperature !== null ? ` · temperature ${detail.temperature}` : ''}${detail.effort ? ` · effort ${detail.effort}` : ''}`,
    },
    { label: 'Prompt', value: detail.prompt_version ?? 'not recorded' },
    {
      label: 'Tokens in / out',
      value:
        detail.tokens_in !== null && detail.tokens_out !== null
          ? `${detail.tokens_in.toLocaleString('en-US')} / ${detail.tokens_out.toLocaleString('en-US')}`
          : 'not recorded',
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
        <CardBoundary name="Call details">
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
          <span role="group" aria-label="Move through the list" style={{ display: 'inline-flex', gap: '6px' }}>
            <button
              type="button"
              onClick={onNewer}
              disabled={!canNewer}
              title="Newer call (K or ↑)"
              aria-label="Newer call"
              style={{ ...GHOST_BUTTON, height: '32px', fontSize: 'var(--sa-text-md)', opacity: canNewer ? 1 : 0.45 }}
            >
              ↑ Newer
            </button>
            <button
              type="button"
              onClick={onOlder}
              disabled={!canOlder}
              title="Older call (J or ↓)"
              aria-label="Older call"
              style={{ ...GHOST_BUTTON, height: '32px', fontSize: 'var(--sa-text-md)', opacity: canOlder ? 1 : 0.45 }}
            >
              ↓ Older
            </button>
          </span>
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
                  : detail.source === 'ask'
                    ? 'Open the chat'
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
            <AttentionChip
              level={detail.attention}
              why={detail.reasons
                .map((r) => REASON_LABELS[r] ?? labelFor(REASON_CODES, r))
                .join(' · ')}
            />
            <b style={{ fontWeight: 600 }}>{detail.result}</b>
            {detail.reasons.length > 0 && (
              <code style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                {detail.reasons.join(' · ')}
              </code>
            )}
          </div>
          {(detail.explanation ?? (detail.outcome === 'success' ? 'No warnings.' : null)) && (
            <div style={{ color: 'var(--sa-text-2)', marginTop: '6px' }}>
              {detail.explanation ?? 'No warnings.'}
            </div>
          )}
          {detail.model_action &&
            detail.final_action &&
            detail.model_action !== detail.final_action && (
              <div style={{ color: 'var(--sa-text-2)', marginTop: '6px' }}>
                The model answered{' '}
                <code style={{ fontFamily: 'var(--sa-font-mono)' }}>{detail.model_action}</code> →
                code made it{' '}
                <code style={{ fontFamily: 'var(--sa-font-mono)' }}>{detail.final_action}</code>
              </div>
            )}
        </div>
        {(detail.explanations ?? []).map((w) => (
          <div
            key={w.code}
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: '10px',
              padding: '14px 16px',
              border: '1px solid var(--sa-border)',
              borderLeft: `3px solid ${
                w.code === 'changed_by_code'
                  ? 'var(--sa-accent)'
                  : detail.outcome === 'error'
                    ? 'var(--sa-negative)'
                    : 'var(--sa-warning-text)'
              }`,
              borderRadius: 'var(--sa-radius-md)',
              background: 'var(--sa-bg)',
            }}
          >
            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: '6px 10px' }}>
              <b style={{ fontWeight: 600, fontSize: 'var(--sa-text-md)' }}>
                What happened · {w.title}
              </b>
              <code style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                {w.code}
              </code>
            </div>
            <div className="insp-kv">
              <span style={{ color: 'var(--sa-text-muted)' }}>What it means</span>
              <span style={{ color: 'var(--sa-text-2)' }}>{w.means}</span>
              <span style={{ color: 'var(--sa-text-muted)' }}>Why here</span>
              <span style={{ color: 'var(--sa-text-2)' }}>
                {w.cause}
                {w.detail ? ` Here: ${w.detail}.` : ''}
              </span>
              <span style={{ color: 'var(--sa-text-muted)' }}>What to do</span>
              <span style={{ color: 'var(--sa-text)' }}>{w.fix}</span>
            </div>
            {w.links.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                {w.links.map((l) => (
                  <Link
                    key={l.href + l.label}
                    to={l.href}
                    style={{ ...GHOST_BUTTON, height: '32px', fontSize: 'var(--sa-text-md)', textDecoration: 'none' }}
                  >
                    {l.label}
                  </Link>
                ))}
              </div>
            )}
          </div>
        ))}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: '10px' }}>
          {stats.map((stat) => (
            <div key={stat.label} style={{ padding: '10px', border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-md)', minWidth: 0 }}>
              <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{stat.label}</div>
              <div style={{ fontFamily: 'var(--sa-font-mono)', marginTop: '4px', overflowWrap: 'anywhere' }}>{stat.value}</div>
            </div>
          ))}
        </div>
        </CardBoundary>
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
          <CardBoundary name="Error">
          <h3 style={{ margin: 0, fontSize: 'var(--sa-text-md)', fontWeight: 600, color: 'var(--sa-text-2)', textTransform: 'uppercase', letterSpacing: 'var(--sa-label-tracking)' }}>
            Error
          </h3>
          <MonoPre>{errorLine || '—'}</MonoPre>
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
            The request was sent but no usable response came back, so there is
            no raw response. Keys and headers are never stored.
          </span>
          </CardBoundary>
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
        <CardBoundary name="Prompt">
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
          {detail.source === 'review'
            ? 'User · facts computed by code'
            : detail.source === 'ask'
              ? 'User · question and context'
              : 'User · input snapshot as sent to the model'}
        </div>
        {detail.prompt.user !== null ? (
          <StructuredView text={detail.prompt.user} />
        ) : (
          <span style={{ color: 'var(--sa-text-muted)' }}>—</span>
        )}
        {detail.prompt.stored !== null && detail.prompt.stored !== detail.prompt.user && (
          <details style={{ fontSize: 'var(--sa-text-sm)' }}>
            <summary style={{ cursor: 'pointer', color: 'var(--sa-text-muted)' }}>
              Stored input
            </summary>
            <MonoPre>{detail.prompt.stored}</MonoPre>
          </details>
        )}
        </CardBoundary>
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
          <CardBoundary name="Raw response">
          <h3 style={{ margin: 0, fontSize: 'var(--sa-text-md)', fontWeight: 600, color: 'var(--sa-text-2)', textTransform: 'uppercase', letterSpacing: 'var(--sa-label-tracking)' }}>
            Raw response
          </h3>
          <StructuredView text={detail.raw_response} />
          </CardBoundary>
        </section>
      )}
    </>
  );
}

// --- Structured input / answer rendering (item 074) -------------------------

const FIELD_LABELS: Record<string, string> = {
  action: 'Action',
  confidence: 'Confidence',
  stop: 'Stop',
  target: 'Target',
  thesis: 'Thesis',
  invalidation: 'Invalidation',
  risks: 'Risks',
  headlines: 'Headlines',
  symbol: 'Symbol',
  price: 'Price',
  question: 'Question',
  answer: 'Answer',
  kind: 'Kind',
  model: 'Model',
  session: 'Session',
  context: 'Context',
};

function fieldLabel(key: string): string {
  return (
    FIELD_LABELS[key] ??
    key
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

const SNAKE_VALUE = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$/;

function scalarText(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'object') return JSON.stringify(value);
  const text = String(value);
  // Enum-like values ("swing_high") read as words, like the keys do.
  return typeof value === 'string' && SNAKE_VALUE.test(text)
    ? fieldLabel(text)
    : text;
}

function JsonNode({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || typeof value !== 'object') {
    return <span style={{ color: 'var(--sa-text-2)' }}>{scalarText(value)}</span>;
  }
  if (Array.isArray(value)) {
    if (value.every((v) => v === null || typeof v !== 'object')) {
      return (
        <ul style={{ margin: 0, paddingLeft: '18px', color: 'var(--sa-text-2)' }}>
          {value.map((v, i) => (
            <li key={i}>{scalarText(v)}</li>
          ))}
        </ul>
      );
    }
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        {value.map((v, i) => (
          <JsonNode key={i} value={v} depth={depth + 1} />
        ))}
      </div>
    );
  }
  const entries = Object.entries(value as Record<string, unknown>);
  const scalars = entries.filter(([, v]) => v === null || typeof v !== 'object');
  const nested = entries.filter(([, v]) => v !== null && typeof v === 'object');
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
      {scalars.length > 0 && (
        <div className="insp-kv">
          {scalars.map(([k, v]) => (
            <Fragment key={k}>
              <span style={{ color: 'var(--sa-text-muted)' }}>{fieldLabel(k)}</span>
              <span style={{ color: 'var(--sa-text-2)', whiteSpace: 'pre-wrap' }}>
                {scalarText(v)}
              </span>
            </Fragment>
          ))}
        </div>
      )}
      {nested.map(([k, v]) => (
        <section key={k} style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <h4
            style={{
              margin: 0,
              fontSize: 'var(--sa-text-sm)',
              fontWeight: 600,
              color: 'var(--sa-text-muted)',
              textTransform: 'uppercase',
              letterSpacing: 'var(--sa-label-tracking)',
            }}
          >
            {fieldLabel(k)}
          </h4>
          <JsonNode value={v} depth={depth + 1} />
        </section>
      ))}
    </div>
  );
}

/** Input snapshot / raw response: sections by default, raw JSON a click away. */
function StructuredView({ text }: { text: string }) {
  const parsed = useMemo(() => {
    try {
      return JSON.parse(text) as unknown;
    } catch {
      return undefined;
    }
  }, [text]);
  const [raw, setRaw] = useState(false);
  const structured = parsed !== undefined && parsed !== null && typeof parsed === 'object';
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
        {structured && (
          <button
            type="button"
            onClick={() => setRaw((v) => !v)}
            aria-pressed={raw}
            style={{ ...GHOST_BUTTON, height: '28px', fontSize: 'var(--sa-text-sm)' }}
          >
            {raw ? 'Show as sections' : 'Raw JSON'}
          </button>
        )}
        <button
          type="button"
          onClick={() => void navigator.clipboard?.writeText(text)}
          style={{ ...GHOST_BUTTON, height: '28px', fontSize: 'var(--sa-text-sm)' }}
        >
          Copy
        </button>
      </div>
      {structured && !raw ? (
        <JsonNode value={parsed} />
      ) : (
        <MonoPre>{text}</MonoPre>
      )}
    </div>
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
        overflowX: 'auto', minWidth: 0,
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

  // At 1181 px and up the design opens the newest call in Call details
  // straight away; narrower screens keep the "select a call" hint (056).
  useEffect(() => {
    if (filters.call !== undefined || listLoading || page.calls.length === 0) return;
    if (!window.matchMedia('(min-width: 1181px)').matches) return;
    const newest =
      filters.order === 'asc' ? page.calls[page.calls.length - 1] : page.calls[0];
    update({ call: newest.id });
  }, [filters.call, filters.order, listLoading, page.calls, update]);

  // ↑ Newer / ↓ Older walk the loaded page; K/J and the arrow keys do
  // the same (item 074). The list is newest-first unless order=asc.
  const selectedIdx = page.calls.findIndex(
    (c) => String(c.id) === String(filters.call),
  );
  const newerIdx =
    selectedIdx < 0
      ? -1
      : filters.order === 'asc'
        ? selectedIdx + 1
        : selectedIdx - 1;
  const olderIdx =
    selectedIdx < 0
      ? -1
      : filters.order === 'asc'
        ? selectedIdx - 1
        : selectedIdx + 1;
  const canNewer = newerIdx >= 0 && newerIdx < page.calls.length;
  const canOlder = olderIdx >= 0 && olderIdx < page.calls.length;
  const goTo = useCallback(
    (idx: number) => {
      const call = page.calls[idx];
      if (call) update({ call: call.id });
    },
    [page.calls, update],
  );
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (
        t &&
        (t.tagName === 'INPUT' ||
          t.tagName === 'TEXTAREA' ||
          t.tagName === 'SELECT' ||
          t.isContentEditable)
      ) {
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey || selectedIdx < 0) return;
      if (e.key === 'j' || e.key === 'J' || e.key === 'ArrowDown') {
        e.preventDefault();
        if (canOlder) goTo(olderIdx);
      } else if (e.key === 'k' || e.key === 'K' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (canNewer) goTo(newerIdx);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selectedIdx, canNewer, canOlder, newerIdx, olderIdx, goTo]);

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
        <CardBoundary name="Filtered calls">
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
              onPickReason={(filter) =>
                update({
                  reason: filter.startsWith('reason=')
                    ? filter.slice('reason='.length)
                    : filter,
                })
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
          <div className="sa-loading">
            <SkeletonLines bars={4} />
          </div>
        )}
        </CardBoundary>
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
          <CardBoundary name="Model calls">
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
              <div className="sa-loading" style={{ padding: 'var(--sa-space-4)' }}>
                <SkeletonList rows={8} />
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
                    {foldCalls(group.calls, filters.call).map((item) =>
                      item.type === 'call' ? (
                        <CallListRow
                          key={item.call.id}
                          call={item.call}
                          selected={item.call.id === filters.call}
                          onSelect={() => update({ call: item.call.id })}
                        />
                      ) : (
                        <FoldRow
                          key={item.key}
                          item={item}
                          selectedId={filters.call}
                          onSelect={(id) => update({ call: id })}
                        />
                      ),
                    )}
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
                {loadingMore ? (
                  <>
                    <span className="sa-spin" aria-hidden="true" />
                    Loading more
                  </>
                ) : (
                  `Load ${Math.min(PAGE_SIZE, page.total - page.calls.length)} more`
                )}
              </button>
            )}
          </div>
          </CardBoundary>
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
              skeleton={
                <section
                  aria-label="Call details"
                  aria-busy="true"
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
                  <span className="sa-sr">Loading call details</span>
                  <SkeletonLines bars={6} />
                </section>
              }
            >
              {(d) => (
                <CallDetails
                  detail={d}
                  onCopy={copyLink}
                  copied={copied}
                  onBack={phone ? () => update({ call: undefined }) : null}
                  onNewer={() => goTo(newerIdx)}
                  onOlder={() => goTo(olderIdx)}
                  canNewer={canNewer}
                  canOlder={canOlder}
                />
              )}
            </CardBody>
          )}
        </div>
      </div>
    </div>
  );
}
