import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import type { Location } from 'react-router-dom';

import { getBacktestDecision, getDecision, postVerdict } from '../api/state';
import { ApiError } from '../api/client';
import { useLiveUpdates } from '../api/liveContext';
import { RecordNotFound } from '../components/errorPages';
import { Chip } from '../components/Chip';
import { displayDayTime, displayTime, elapsedHm, signed } from '../format';
import { DialogButton } from '../components/Modal';
import { SkeletonLines } from '../components/cardState';
import type { DecisionDetail, VerdictValue, V2Level, V2Option, V2StopOption, V3Checks, V3Position } from '../types/api';
import { useAsk } from '../ask/AskContext';
import { useMediaQuery } from '../hooks/useMediaQuery';
import {
  dueDayLabel,
  verdictLabel,
  verdictVariant,
} from './reviewsFormat';
import {
  actionBadgeStyle,
  actionLabel,
  contextLabel,
  formatAmsDateTime,
  formatConfidence,
  formatNyTitle,
} from '../components/decisionFormat';
import { actionText } from './dashboardFeed';
import {
  firstTouchLabel,
  checkTurnedAdverse,
  earningsChipLabel,
  gateRows,
  num,
  orderStatusText,
  selectionCycleBlock,
  signalRows,
  signedPct,
  sipBarEnd,
} from './decisionDetail';
import { ACTIONS, REASON_CODES, labelFor } from '../labels';

// ---------------------------------------------------------------------------
// shared bits

function SectionHeader({
  label,
  right,
}: {
  label: string;
  right?: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'baseline',
        gap: 'var(--sa-space-2)',
      }}
    >
      <h3
        style={{
          margin: 0,
          fontSize: 'var(--sa-text-sm)',
          fontWeight: 600,
          color: 'var(--sa-text-muted)',
          textTransform: 'uppercase',
          letterSpacing: 'var(--sa-label-tracking)',
        }}
      >
        {label}
      </h3>
      {right && (
        <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          {right}
        </span>
      )}
    </div>
  );
}

function Section({
  label,
  right,
  children,
}: {
  label: string;
  right?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section
      aria-label={label}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sa-space-2)',
        padding: 'var(--sa-space-4) 0',
        borderTop: '1px solid var(--sa-border)',
      }}
    >
      <SectionHeader label={label} right={right} />
      {children}
    </section>
  );
}

function muted(text: React.ReactNode) {
  return (
    <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
      {text}
    </div>
  );
}

// ---------------------------------------------------------------------------
// header + action row

function RegimeChip({ regime }: { regime: unknown }) {
  const r = regime as { trend?: string; volatility?: string } | null;
  if (!r?.trend) return null;
  const variant =
    r.trend === 'up' ? 'positive' : r.trend === 'down' ? 'negative' : 'neutral';
  return (
    <span title="SPY regime: trend × volatility">
      <Chip variant={variant}>
        market {r.trend} · {r.volatility} vol
      </Chip>
    </span>
  );
}

interface GatesResult {
  allowed?: boolean;
  blocked_by?: string | null;
  risk_factor?: string;
  sizing?: { shares?: number; risk_budget?: string; limited_by?: string[] } | null;
  checks?: { name: string; passed: boolean; detail: string }[];
  pending_approval?: boolean;
}

// ---------------------------------------------------------------------------
// safety gates

function gateStateColor(state: 'pass' | 'fail' | 'reduced' | 'warning'): string {
  return state === 'pass'
    ? 'var(--sa-positive)'
    : state === 'fail'
      ? 'var(--sa-negative-text)'
      : 'var(--sa-warning-text)';
}

function SafetyGates({ decision }: { decision: DecisionDetail }) {
  const rows = gateRows(decision);
  return (
    <Section label="Safety gates" right="code checks, then risk gates in order">
      {rows.length === 0 ? (
        muted(
          decision.rules_result?.reason ??
            decision.validation_result?.error ??
            'No checks recorded — the model was not asked this cycle.',
        )
      ) : (
        <div
          style={{
            border: '1px solid var(--sa-border)',
            borderRadius: 'var(--sa-radius-md)',
            padding: '0 var(--sa-space-3)',
          }}
        >
          {rows.map((row, i) => (
            <div
              key={i}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                gap: 'var(--sa-space-3)',
                padding: '7px 0',
                fontSize: 'var(--sa-text-md)',
                borderBottom:
                  i < rows.length - 1 ? '1px solid var(--sa-border)' : 'none',
              }}
            >
              <span style={{ color: 'var(--sa-text-2)' }}>{row.name}</span>
              <span
                style={{
                  fontFamily: 'var(--sa-font-mono)',
                  fontSize: 'var(--sa-text-sm)',
                  textAlign: 'right',
                  color: gateStateColor(row.state),
                }}
              >
                {row.detail ? `${row.detail} · ` : ''}
                {row.state}
              </span>
            </div>
          ))}
        </div>
      )}
      {(decision.gates_result as GatesResult | null)?.pending_approval && (
        muted('Passed the gates — waiting for approval (supervised mode).')
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// entry selection (PR 33)

function SelectionBlock({ decision }: { decision: DecisionDetail }) {
  const block = selectionCycleBlock(decision);
  if (block === null) return null;
  return (
    <Section
      label="Selection in this cycle"
      right={`${block.freeSlots} slots free · ${block.scoredCount} entries passed`}
    >
      <div
        style={{
          border: '1px solid var(--sa-border)',
          borderRadius: 'var(--sa-radius-md)',
          padding: '0 var(--sa-space-3)',
        }}
      >
        {block.rows.map((row, i) => (
          <div
            key={row.id}
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              gap: 'var(--sa-space-3)',
              padding: '7px 0',
              fontSize: 'var(--sa-text-md)',
              borderBottom:
                i < block.rows.length - 1 ? '1px solid var(--sa-border)' : 'none',
            }}
          >
            <span style={{ color: 'var(--sa-text)' }}>
              {row.symbol}{' '}
              <span style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}>
                {row.rr} R/R · p {row.p}
              </span>
            </span>
            <span
              style={{
                fontFamily: 'var(--sa-font-mono)',
                fontSize: 'var(--sa-text-sm)',
                textAlign: 'right',
                color: 'var(--sa-text-2)',
              }}
            >
              ${row.risk} · {row.expectedR} R · ${row.expectedProfit}{' '}
              <span
                style={{
                  color: row.chosen
                    ? 'var(--sa-positive)'
                    : 'var(--sa-warning-text)',
                }}
              >
                {row.chosen ? 'selected' : 'not selected'}
              </span>
            </span>
          </div>
        ))}
      </div>
      {muted(
        `Selected: ${block.chosenSymbols.join(' + ') || '—'}, ${block.total === '—' ? '—' : `$${block.total}`}. ` +
          `Rank order would have taken ${block.rankSymbols.join(' + ') || '—'}, ` +
          `${block.rankTotal === '—' ? '—' : `$${block.rankTotal}`}.`,
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// signal stack

function SignalStack({ decision }: { decision: DecisionDetail }) {
  const snapshot = decision.input_snapshot ?? {};
  const daily = (snapshot.indicators_daily ?? {}) as Record<string, unknown>;
  const levels = snapshot.levels as
    | { swing_highs?: number[]; swing_lows?: number[] }
    | string
    | undefined;
  const rows = signalRows(snapshot);

  const chips: string[] = [];
  const gap = num(daily.gap_pct);
  if (gap !== null) chips.push(`Gap ${signedPct(gap)}`);
  if (levels && typeof levels === 'object') {
    const high = levels.swing_highs?.length
      ? Math.max(...levels.swing_highs)
      : null;
    const low = levels.swing_lows?.length ? Math.min(...levels.swing_lows) : null;
    if (high !== null || low !== null) {
      chips.push(`Swing high ${high ?? '—'} · low ${low ?? '—'}`);
    }
  }
  const earnings = decision.earnings;
  if (earnings && !earnings.date) chips.push(earningsChipLabel(earnings));
  if (earnings?.date) {
    const date = new Date(`${earnings.date}T00:00:00Z`).toLocaleDateString(
      'en-GB',
      { day: 'numeric', month: 'short', timeZone: 'UTC' },
    );
    chips.push(
      `Earnings ${date}${
        earnings.sessions !== null ? ` · ${earnings.sessions} sessions` : ''
      }`,
    );
  }

  return (
    <Section label="Signal stack" right={`SIP bars to ${sipBarEnd(decision.cycle_time)} NY`}>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1.4fr) minmax(0, 1fr) minmax(0, 1fr)',
          gap: '4px var(--sa-space-3)',
          fontSize: 'var(--sa-text-md)',
        }}
      >
        {['Signal', '1h', 'Daily'].map((head, i) => (
          <span
            key={head}
            style={{
              fontSize: 'var(--sa-text-sm)',
              color: 'var(--sa-text-muted)',
              textAlign: i === 0 ? 'left' : 'right',
              paddingBottom: '4px',
              borderBottom: '1px solid var(--sa-border)',
            }}
          >
            {head}
          </span>
        ))}
        {rows.map(([name, h1, d1]) => (
          <Fragment key={name}>
            <span style={{ color: 'var(--sa-text-2)', padding: '4px 0' }}>
              {name}
            </span>
            <span
              style={{
                fontFamily: 'var(--sa-font-mono)',
                textAlign: 'right',
                color: 'var(--sa-text-2)',
                padding: '4px 0',
              }}
            >
              {h1}
            </span>
            <span
              style={{
                fontFamily: 'var(--sa-font-mono)',
                textAlign: 'right',
                color: 'var(--sa-text-2)',
                padding: '4px 0',
              }}
            >
              {d1}
            </span>
          </Fragment>
        ))}
      </div>
      {chips.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
          {chips.map((text) => (
            <Chip key={text} variant="neutral">
              {text}
            </Chip>
          ))}
        </div>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// reasoning

interface Headline {
  id?: string;
  time?: string;
  source?: string;
  title?: string;
}

function Reasoning({ decision }: { decision: DecisionDetail }) {
  const output = decision.model_output;
  const rulesReason = decision.rules_result?.reason;
  const error = decision.validation_result?.error;
  const thesis = output?.thesis || rulesReason || error || null;

  const headlines = Array.isArray(decision.input_snapshot?.headlines)
    ? (decision.input_snapshot?.headlines as Headline[])
    : [];
  const news = (output?.news_used ?? [])
    .map((n) => {
      const id = typeof n === 'string' ? n : n.id;
      const found = headlines.find((h) => h.id === id);
      return found
        ? <span key={id} title={found.time ? formatNyTitle(found.time) : undefined}>“{found.title}” · {found.source} · {displayTime(found.time)}</span>
        : <span key={id}>{id}</span>;
    })
    .filter(Boolean);

  return (
    <Section label="Agent reasoning">
      {thesis ? (
        <p
          style={{
            margin: 0,
            fontSize: 'var(--sa-text-md)',
            color: 'var(--sa-text-2)',
            lineHeight: 1.55,
          }}
        >
          {thesis}
        </p>
      ) : (
        muted('No model reasoning recorded.')
      )}
      {output && output.risks.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
          {output.risks.map((risk) => (
            <Chip key={risk} variant="warning" wrap>
              Risk: {risk}
            </Chip>
          ))}
        </div>
      )}
      {news.length > 0 && (
        <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          News used: <span style={{ color: 'var(--sa-text-2)' }}>{news.map((item, index) => <Fragment key={item.key}>{index > 0 && ' · '}{item}</Fragment>)}</span>
        </div>
      )}
    </Section>
  );
}


// ---------------------------------------------------------------------------
// decision.v2: code-built options (design/screens/DecisionV2.dc.html)

const MONO = 'var(--sa-font-mono)';

function _v2Levels(decision: DecisionDetail): V2Level[] {
  const stored = decision.levels?.levels;
  if (Array.isArray(stored)) return stored as V2Level[];
  const snap = decision.input_snapshot?.levels;
  return Array.isArray(snap) ? (snap as V2Level[]) : [];
}

function _v2Options(decision: DecisionDetail): V2Option[] {
  const col = decision.options?.options;
  if (Array.isArray(col)) return col;
  const snap = decision.input_snapshot?.options;
  return Array.isArray(snap) ? (snap as V2Option[]) : [];
}

function _v2StopOptions(decision: DecisionDetail): V2StopOption[] {
  const col = decision.options?.stop_options;
  if (Array.isArray(col)) return col;
  const snap = decision.input_snapshot?.stop_options;
  return Array.isArray(snap) ? (snap as V2StopOption[]) : [];
}

function V2OptionsTable({ decision }: { decision: DecisionDetail }) {
  const options = _v2Options(decision);
  const stopOptions = _v2StopOptions(decision);
  const chosen = decision.chosen_option;
  const merged = decision.options?.merged ?? [];
  if (options.length === 0 && stopOptions.length === 0) {
    if (decision.context === 'held') return null;
    return (
      <Section label="Options offered" right="built by code — the model picks an id">
        {muted('No option met the stop band and R/R rules — the model was not asked.')}
      </Section>
    );
  }
  return (
    <Section label="Options offered" right="built by code — the model picks an id">
      <div
        style={{
          border: '1px solid var(--sa-border)',
          borderRadius: 'var(--sa-radius-md)',
          overflowX: 'auto', minWidth: 0,
        }}
      >
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--sa-border)' }}>
              {['', 'Stop', 'Target', 'R/R'].map((h) => (
                <th
                  key={h}
                  style={{
                    textAlign: 'left',
                    padding: '8px 10px',
                    fontSize: 'var(--sa-text-sm)',
                    color: 'var(--sa-text-muted)',
                    fontWeight: 500,
                  }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {options.map((o) => (
              <tr
                key={o.id}
                style={{
                  borderBottom: '1px solid var(--sa-border)',
                  background:
                    o.id === chosen ? 'var(--sa-accent-bg, rgba(127,184,255,0.10))' : undefined,
                }}
              >
                <td
                  style={{
                    padding: '8px 10px',
                    fontFamily: MONO,
                    fontWeight: 600,
                    color:
                      o.id === chosen ? 'var(--sa-accent)' : 'var(--sa-text-2)',
                  }}
                >
                  {o.id}
                </td>
                <td style={{ padding: '8px 10px', fontSize: 'var(--sa-text-sm)' }}>
                  <div style={{ fontFamily: MONO }}>{o.stop.toFixed(2)}</div>
                  <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-xs, 11px)' }}>
                    {o.stop_basis}
                    {o.stop_atr != null ? ` · ${o.stop_atr.toFixed(2)} ATR` : ''}
                  </div>
                </td>
                <td style={{ padding: '8px 10px', fontSize: 'var(--sa-text-sm)' }}>
                  <div style={{ fontFamily: MONO }}>{o.target.toFixed(2)}</div>
                  <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-xs, 11px)' }}>
                    {o.target_basis}
                    {(o.levels_in_way ?? []).length > 0
                      ? ` · in the way ${o.levels_in_way.join(', ')}`
                      : ''}
                  </div>
                </td>
                <td
                  style={{
                    padding: '8px 10px',
                    fontFamily: MONO,
                    fontSize: 'var(--sa-text-sm)',
                  }}
                >
                  {o.rr.toFixed(2)}
                </td>
              </tr>
            ))}
            {stopOptions.map((o) => (
              <tr
                key={o.id}
                style={{
                  borderBottom: '1px solid var(--sa-border)',
                  background:
                    o.id === chosen ? 'var(--sa-accent-bg, rgba(127,184,255,0.10))' : undefined,
                }}
              >
                <td
                  style={{
                    padding: '8px 10px',
                    fontFamily: MONO,
                    fontWeight: 600,
                    color:
                      o.id === chosen ? 'var(--sa-accent)' : 'var(--sa-text-2)',
                  }}
                >
                  {o.id}
                </td>
                <td style={{ padding: '8px 10px', fontSize: 'var(--sa-text-sm)' }} colSpan={2}>
                  <span style={{ fontFamily: MONO }}>{o.stop.toFixed(2)}</span>
                  <span style={{ color: 'var(--sa-text-muted)' }}>
                    {' '}
                    · {o.basis}
                  </span>
                </td>
                <td
                  style={{
                    padding: '8px 10px',
                    fontFamily: MONO,
                    fontSize: 'var(--sa-text-sm)',
                  }}
                >
                  {o.r_at_stop != null ? `${signed(o.r_at_stop)}R` : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {merged.length > 0 &&
        muted(`Merged: ${merged.join('; ')}`)}
    </Section>
  );
}

function _levelKindLabel(level: V2Level): string {
  const when = level.time
    ? new Date(level.time).toLocaleDateString('en-GB', {
        day: 'numeric',
        month: 'short',
        timeZone: 'UTC',
      })
    : '';
  const label: Record<string, string> = {
    swing_low: `${level.timeframe} swing low`,
    swing_high: `${level.timeframe} swing high`,
    prior_session_high: 'prior session high',
    prior_session_low: 'prior session low',
    prior_session_close: 'prior session close',
    session_high: 'session high so far',
    session_low: 'session low so far',
    vwap: 'session VWAP',
  };
  return `${label[level.kind] ?? level.kind}${when ? ` · ${when}` : ''}`;
}

function V2Levels({ decision }: { decision: DecisionDetail }) {
  const levels = _v2Levels(decision);
  const notes = decision.options?.level_notes ?? {};
  const used = new Set(decision.model_output?.levels_used ?? []);
  const held = decision.context === 'held';
  const rawLimit = held ? decision.input_snapshot?.price
    : (decision.input_snapshot?.constraints as Record<string, unknown> | undefined)?.entry_limit;
  const limit = typeof rawLimit === 'number' && Number.isFinite(rawLimit) ? rawLimit : null;
  if (levels.length === 0) return null;
  const firstBelow = limit === null ? -1 : levels.findIndex((lv) => lv.price < limit);
  const limitIdx = limit === null ? -1 : firstBelow === -1 ? levels.length : firstBelow;
  const priceLine = limit !== null && (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '7px 0',
      borderBottom: '1px dashed var(--sa-accent)', fontSize: 'var(--sa-text-sm)',
      color: 'var(--sa-accent)', fontFamily: MONO }}>
      <span>{held ? 'price' : 'entry limit'}</span><span>{limit.toFixed(2)}</span>
    </div>
  );
  return (
    // Folded by default on every version (design: "Levels · n · the
    // summary gives the count and the bar time").
    <details
      style={{
        marginTop: 'var(--sa-space-5)',
        border: '1px solid var(--sa-border)',
        borderRadius: 'var(--sa-radius-md)',
        padding: '0 var(--sa-space-3)',
      }}
    >
      <summary
        style={{
          cursor: 'pointer',
          minHeight: 'var(--sa-control-height)',
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--sa-space-3)',
          fontSize: 'var(--sa-text-md)',
          fontWeight: 600,
          color: 'var(--sa-text-2)',
          textTransform: 'uppercase',
          letterSpacing: 'var(--sa-label-tracking)',
        }}
      >
        Levels · {levels.length} · from 1h and daily bars to {sipBarEnd(decision.cycle_time)} NY
      </summary>
      <div>
        {levels.map((lv, i) => (
          <Fragment key={lv.id}>
            {i === limitIdx && priceLine}
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                gap: 'var(--sa-space-3)',
                padding: '7px 0',
                fontSize: 'var(--sa-text-md)',
                borderBottom:
                  i < levels.length - 1 ? '1px solid var(--sa-border)' : 'none',
              }}
            >
              <span style={{ display: 'flex', gap: '8px', minWidth: 0 }}>
                <b
                  style={{
                    fontFamily: MONO,
                    color: used.has(lv.id) ? 'var(--sa-accent)' : 'var(--sa-text-2)',
                  }}
                >
                  {lv.id}
                </b>
                <span style={{ fontFamily: MONO, color: 'var(--sa-text)' }}>
                  {lv.price.toFixed(2)}
                </span>
                <span
                  style={{
                    color: 'var(--sa-text-muted)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {_levelKindLabel(lv)}
                </span>
              </span>
              <span
                style={{
                  fontSize: 'var(--sa-text-sm)',
                  color: 'var(--sa-text-muted)',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  maxWidth: '40%',
                  flexShrink: 0,
                }}
              >
                {notes[lv.id] ?? ''}
              </span>
            </div>
          </Fragment>
        ))}
        {limitIdx === levels.length && priceLine}
      </div>
      <div
        style={{
          fontSize: 'var(--sa-text-sm)',
          color: 'var(--sa-text-muted)',
          padding: 'var(--sa-space-2) 0 var(--sa-space-3)',
        }}
      >
        Nearest first on each side of the price.
      </div>
    </details>
  );
}

// ---------------------------------------------------------------------------
// decision.v3: checks by code, room and break-even
// (design/screens/DecisionV3.dc.html, DecisionV3Held.dc.html)

// decision.v5 renders the decision.v4 drawer — same schema and layout
// (docs/SPEC.md, Decision v5 — the output schema is v4's).
const _isV4Plus = (version: string | null | undefined): boolean =>
  version === 'decision.v4' || version === 'decision.v5';

function _v3Checks(decision: DecisionDetail): V3Checks | null {
  const checks = decision.input_snapshot?.checks;
  return checks && typeof checks === 'object' ? (checks as V3Checks) : null;
}

function _checkCard(
  label: string,
  value: string,
  sub: string,
  accent?: string,
) {
  return (
    <div
      key={label}
      style={{
        padding: '10px 12px',
        border: '1px solid var(--sa-border)',
        borderRadius: 'var(--sa-radius-md)',
        display: 'flex',
        flexDirection: 'column',
        gap: '4px',
        minWidth: 0,
      }}
    >
      <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
        {label}
      </span>
      <span
        style={{
          fontFamily: MONO,
          fontSize: 'var(--sa-text-md)',
          color: accent ?? 'var(--sa-text)',
        }}
      >
        {value}
      </span>
      <span
        style={{
          fontSize: 'var(--sa-text-sm)',
          color: 'var(--sa-text-muted)',
          overflowWrap: 'anywhere',
        }}
      >
        {sub}
      </span>
    </div>
  );
}

function _trendAccent(value?: string): string | undefined {
  if (value === 'up' || value === 'strong') return 'var(--sa-positive)';
  if (value === 'down' || value === 'weak') return 'var(--sa-negative-text)';
  if (value === 'mixed') return 'var(--sa-warning)';
  return undefined;
}

function V3Checks({ decision }: { decision: DecisionDetail }) {
  const checks = _v3Checks(decision);
  if (checks === null) return null;
  const previous = _isV4Plus(decision.prompt_version) ? _v4Previous(decision) : null;
  const changes = previous?.since?.checks_changed ?? [];
  const minRoom = Number((decision.input_snapshot?.constraints as Record<string, unknown> | undefined)?.min_room_atr);
  const roomTooSmall = checks.room_atr != null && Number.isFinite(minRoom) && Number(checks.room_atr) < minRoom;
  const earnings = checks.earnings ?? {};
  const earningsText =
    earnings.days === null || earnings.days === undefined
      ? '—'
      : `${earnings.days} trading day${earnings.days === 1 ? '' : 's'}${
          earnings.date
            ? ` · ${new Date(`${earnings.date}T12:00:00Z`).toLocaleDateString(
                'en-GB',
                { day: 'numeric', month: 'short', timeZone: 'UTC' },
              )}${earnings.time_of_day ? ` ${String(earnings.time_of_day).replace('_', ' ')}` : ''}`
            : ''
        }`;
  return (
    <Section label="Checks by code" right="facts the model was given">
      {previous?.since && <div style={{ fontSize: 'var(--sa-text-sm)', overflowWrap: 'anywhere' }}>
        Compared with previous cycle: {changes.length > 0
          ? `${changes.join(' · ')}. No other recorded check changes.`
          : 'no recorded check changes.'}
      </div>}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))',
          gap: 'var(--sa-space-2)',
        }}
      >
        {_checkCard('Trend 1h', checks.trend_1h ?? '—', 'price vs EMA 20 · EMA 50', _trendAccent(checks.trend_1h))}
        {_checkCard('Trend daily', checks.trend_daily ?? '—', 'close vs SMA 50 · SMA 200', _trendAccent(checks.trend_daily))}
        {_checkCard(
          'Market regime',
          `${checks.regime_trend ?? '—'} · ${checks.regime_volatility ?? '—'} vol`,
          'SPY trend and volatility',
        )}
        {_checkCard(
          'Room to first level',
          checks.room_atr != null
            ? `${Number(checks.room_atr).toFixed(2)} ATR${checks.room_level ? ` · ${checks.room_level}` : ''}`
            : 'no level in the way',
          Number.isFinite(minRoom) ? `minimum ${minRoom} ATR${roomTooSmall ? ' · room too small' : ''}` : 'above the entry limit',
          roomTooSmall ? 'var(--sa-warning-text)' : 'var(--sa-text-muted)',
        )}
        {_checkCard(
          'Momentum',
          checks.momentum ?? '—',
          'relative strength vs SPY · OBV',
          _trendAccent(checks.momentum),
        )}
        {_checkCard('Earnings', earningsText, 'next report')}
        {_checkCard('News', checks.news_status ?? '—', 'news status')}
      </div>
    </Section>
  );
}

function _v3WayText(levels_in_way: V2Option['levels_in_way']): string {
  return levels_in_way
    .map((lv) =>
      typeof lv === 'string'
        ? lv
        : `${lv.id} ${lv.distance_atr != null ? lv.distance_atr.toFixed(2) : '—'}`,
    )
    .join(' · ');
}

function V3OptionsTable({ decision }: { decision: DecisionDetail }) {
  const options = _v2Options(decision);
  const chosen = decision.chosen_option;
  // decision.v5: options carry target_atr and min_confidence; v3/v4
  // options don't, and their drawer stays unchanged (docs/SPEC.md).
  const v5Cols = options.some(
    (o) => o.min_confidence != null || o.target_atr != null || o.base_rate != null || o.base_n != null,
  );
  const confidence = decision.model_output?.confidence ?? null;
  const headers = v5Cols
    ? ['', 'Stop', 'Target · ATR · in the way', 'R/R', 'Min conf', 'Base rate', 'Model']
    : ['', 'Stop', 'Target · levels in the way (ATR above the limit)', 'R/R', 'Break-even'];
  return (
    <Section
      label="Options offered"
      right={`${options.length} built by code${chosen ? ` · model chose ${chosen}` : ''}`}
    >
      <div
        style={{
          border: '1px solid var(--sa-border)',
          borderRadius: 'var(--sa-radius-md)',
          overflowX: 'auto', minWidth: 0,
        }}
      >
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--sa-border)' }}>
              {headers.map(
                (h) => (
                  <th
                    key={h}
                    style={{
                      textAlign: 'left',
                      padding: '8px 10px',
                      fontSize: 'var(--sa-text-sm)',
                      color: 'var(--sa-text-muted)',
                      fontWeight: 500,
                    }}
                  >
                    {h}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {options.map((o) => (
              <tr
                key={o.id}
                style={{
                  borderBottom: '1px solid var(--sa-border)',
                  background:
                    o.id === chosen ? 'var(--sa-accent-bg, rgba(127,184,255,0.10))' : undefined,
                }}
              >
                <td
                  style={{
                    padding: '8px 10px',
                    fontFamily: MONO,
                    fontWeight: 600,
                    color: o.id === chosen ? 'var(--sa-accent)' : 'var(--sa-text-2)',
                  }}
                >
                  {o.id}
                  {o.stop_group ? (
                    <span
                      title="Options sharing a stop share a group"
                      style={{
                        marginLeft: '6px',
                        fontSize: 'var(--sa-text-xs, 11px)',
                        color: 'var(--sa-text-muted)',
                      }}
                    >
                      {o.stop_group}
                    </span>
                  ) : null}
                </td>
                <td style={{ padding: '8px 10px', fontSize: 'var(--sa-text-sm)' }}>
                  <div style={{ fontFamily: MONO }}>{o.stop.toFixed(2)}</div>
                  <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-xs, 11px)' }}>
                    {o.stop_basis}
                    {o.stop_atr != null ? ` · ${o.stop_atr.toFixed(2)} ATR` : ''}
                  </div>
                </td>
                <td style={{ padding: '8px 10px', fontSize: 'var(--sa-text-sm)' }}>
                  <div style={{ fontFamily: MONO }}>
                    {o.target.toFixed(2)}
                    {v5Cols && o.target_atr != null && (
                      <span style={{ fontSize: 'var(--sa-text-xs, 11px)', color: 'var(--sa-text-2)' }}>
                        {' '}{o.target_atr.toFixed(1)} ATR
                      </span>
                    )}
                  </div>
                  <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-xs, 11px)' }}>
                    {v5Cols
                      ? (o.levels_in_way ?? []).length > 0
                        ? _v3WayText(o.levels_in_way)
                        : o.target_basis
                      : `${o.target_basis}${(o.levels_in_way ?? []).length > 0 ? ` · in the way ${_v3WayText(o.levels_in_way)}` : ''}`}
                  </div>
                </td>
                <td style={{ padding: '8px 10px', fontFamily: MONO, fontSize: 'var(--sa-text-sm)', textAlign: 'right' }}>
                  {o.rr.toFixed(v5Cols ? 1 : 2)}
                </td>
                {!v5Cols && (
                  <td
                    style={{
                      padding: '8px 10px',
                      fontFamily: MONO,
                      fontSize: 'var(--sa-text-sm)',
                      color: 'var(--sa-text-2)',
                    }}
                  >
                    {o.breakeven_confidence != null ? o.breakeven_confidence.toFixed(2) : '—'}
                  </td>
                )}
                {v5Cols && (
                  <>
                    <td
                      style={{
                        padding: '8px 10px',
                        fontFamily: MONO,
                        fontSize: 'var(--sa-text-sm)',
                        textAlign: 'right',
                        color: 'var(--sa-text-2)',
                      }}
                    >
                      {o.min_confidence != null ? o.min_confidence.toFixed(2) : '—'}
                    </td>
                    <td
                      style={{
                        padding: '8px 10px',
                        fontFamily: MONO,
                        fontSize: 'var(--sa-text-xs, 11px)',
                        textAlign: 'right',
                        color: 'var(--sa-text-2)',
                      }}
                    >
                      {o.base_rate != null && (o.base_n ?? 0) >= 30 ? (
                        <>
                          {o.base_rate.toFixed(2)}{' '}
                          <span style={{ color: 'var(--sa-text-muted)' }}>n {o.base_n}</span>
                        </>
                      ) : (
                        <span
                          title="Fewer than 30 comparable cases: no base rate yet"
                          style={{ color: 'var(--sa-text-muted)' }}
                        >
                          —{o.base_n != null ? ` n ${o.base_n}` : ''}
                        </span>
                      )}
                    </td>
                    <td
                      style={{
                        padding: '8px 10px',
                        fontFamily: MONO,
                        fontSize: 'var(--sa-text-sm)',
                        textAlign: 'right',
                      }}
                    >
                      {o.id === chosen && confidence !== null ? (
                        <span
                          style={{
                            color:
                              o.min_confidence != null && confidence >= o.min_confidence
                                ? 'var(--sa-positive)'
                                : 'var(--sa-negative-text)',
                          }}
                        >
                          {confidence.toFixed(2)}{' '}
                          {o.min_confidence != null
                            ? confidence >= o.min_confidence
                              ? '✓'
                              : '✗'
                            : ''}
                        </span>
                      ) : (
                        <span style={{ color: 'var(--sa-text-muted)' }}>—</span>
                      )}
                    </td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(decision.options?.removed ?? []).length > 0 &&
        muted(
          `Removed by the room filter: ${(decision.options?.removed ?? [])
            .map((r) => `${r.id} — ${r.reason}`)
            .join('; ')}`,
        )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// decision.v4: previous decision + since, v4 conflicts, the held-thesis check
// (design/screens/DecisionV4.dc.html, DecisionV4Held.dc.html)

interface V4ConflictObj {
  text: string;
  outweighed_by: string | null;
}

interface V4Headline {
  id?: string;
  time?: string;
  source?: string;
  title?: string;
  about_symbol?: boolean;
  new?: boolean;
}

interface V4Previous {
  time?: string | null;
  action?: string | null;
  /** The final action code took (v5: shown beside the model's action). */
  final_action?: string | null;
  /** The gate or check behind a change code made (v5). */
  change_reason?: string | null;
  option_id?: string | null;
  best_option_id?: string | null;
  confidence?: number | null;
  conflicts?: string[];
  price?: number | null;
  since?: {
    price_move_atr?: number | null;
    checks_changed?: string[];
    new_headlines?: string[];
    new_levels_within_1_atr?: string[];
  };
}

function _v4Previous(decision: DecisionDetail): V4Previous | null {
  const raw = decision.input_snapshot?.previous_decision;
  return raw && typeof raw === 'object' ? (raw as V4Previous) : null;
}

function _v4Conflicts(decision: DecisionDetail): V4ConflictObj[] {
  return (decision.model_output?.conflicts ?? []).map((c) =>
    typeof c === 'string' ? { text: c, outweighed_by: null } : c,
  );
}

function _v4NewsUsed(decision: DecisionDetail): { id: string; effect: string }[] {
  return (decision.model_output?.news_used ?? []).map((n) =>
    typeof n === 'string' ? { id: n, effect: '' } : n,
  );
}

function _sinceText(since: NonNullable<V4Previous['since']>): string[] {
  const parts: string[] = [];
  if (since.price_move_atr != null) {
    parts.push(`price moved ${signed(Number(since.price_move_atr))} ATR`);
  }
  parts.push(
    since.checks_changed && since.checks_changed.length > 0
      ? `checks changed: ${since.checks_changed.join('; ')}`
      : 'checks the same',
  );
  parts.push(
    since.new_headlines && since.new_headlines.length > 0
      ? `new headlines: ${since.new_headlines.join(', ')}`
      : 'no new headlines',
  );
  parts.push(
    since.new_levels_within_1_atr && since.new_levels_within_1_atr.length > 0
      ? `new levels within 1 ATR: ${since.new_levels_within_1_atr.join(', ')}`
      : 'no new levels within 1 ATR',
  );
  return parts;
}

function V4PreviousDecision({ decision }: { decision: DecisionDetail }) {
  const prev = _v4Previous(decision);
  if (prev === null) return null;
  return (
    <Section
      label="Previous decision"
      right="the model's last answer on this symbol, this session"
    >
      <div
        style={{
          fontFamily: MONO,
          fontSize: 'var(--sa-text-md)',
          color: 'var(--sa-text-2)',
        }}
      >
        {(prev.action ?? '—').toUpperCase()}
        {prev.time && <span title={formatNyTitle(prev.time)}> at {displayTime(prev.time)}</span>}
        {prev.option_id ? ` · option ${prev.option_id}` : ''}
        {prev.best_option_id ? ` · best option ${prev.best_option_id}` : ''}
        {prev.confidence != null
          ? ` · confidence ${Number(prev.confidence).toFixed(2)}`
          : ''}
        {prev.final_action && prev.final_action !== prev.action
          ? ` · final ${labelFor(ACTIONS, prev.final_action)}${prev.change_reason ? ` · ${labelFor(REASON_CODES, prev.change_reason)}` : ''}`
          : ''}
      </div>
      {(prev.conflicts ?? []).length > 0 && (
        <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          conflicts: {(prev.conflicts ?? []).join(' · ')}
        </div>
      )}
      {prev.since && (
        <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          Since: {_sinceText(prev.since).join(' · ')}
        </div>
      )}
    </Section>
  );
}

function V4Reasoning({ decision }: { decision: DecisionDetail }) {
  const output = decision.model_output;
  const validation = decision.validation_result;
  const conflicts = _v4Conflicts(decision);
  const flags = output?.data_flags ?? [];
  const thesis = output?.thesis ?? '';
  const whatChanged = output?.what_changed;
  const levelsUsed = output?.levels_used ?? [];
  const written = validation?.code_written;
  return (
    <Section label="Agent reasoning" right="conflicts first, then the decision">
      {conflicts.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
          {conflicts.map((c) => (
            <div key={c.text} style={{ width: '100%', overflowWrap: 'anywhere', fontSize: 'var(--sa-text-md)' }}>
              <Chip variant="warning" wrap>Conflict: {c.text}</Chip>
              {c.outweighed_by
                ? ` — outweighed by ${c.outweighed_by}`
                : ' — not outweighed'}
            </div>
          ))}
        </div>
      )}
      {flags.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
          {flags.map((flag) => (
            <Chip key={flag} variant="negative">
              Data flag: {flag.replace(/_/g, ' ')}
            </Chip>
          ))}
        </div>
      )}
      {thesis ? (
        <p
          style={{
            margin: 0,
            fontSize: 'var(--sa-text-md)',
            color: 'var(--sa-text-2)',
            lineHeight: 1.55,
          }}
        >
          {thesis}
        </p>
      ) : (
        muted('No model reasoning recorded.')
      )}
      {whatChanged && (
        <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          What changed (model): <span style={{ color: 'var(--sa-text-2)' }}>{whatChanged}</span>
        </div>
      )}
      {written && (
        <div
          style={{
            fontSize: 'var(--sa-text-sm)',
            color: 'var(--sa-text-muted)',
            fontFamily: MONO,
          }}
        >
          {written}
        </div>
      )}
      {levelsUsed.length > 0 && (
        <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          Levels used: {levelsUsed.join(', ')}
        </div>
      )}
    </Section>
  );
}

function V4Headlines({ decision }: { decision: DecisionDetail }) {
  const raw = decision.input_snapshot?.headlines;
  const headlines: V4Headline[] = Array.isArray(raw) ? (raw as V4Headline[]) : [];
  if (headlines.length === 0) return null;
  const effects = new Map(_v4NewsUsed(decision).map((n) => [n.id, n.effect]));
  const about = headlines.filter((h) => h.about_symbol).length;
  return (
    <Section label="Headlines" right={`${headlines.length} · ${about} about ${decision.symbol}`}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
        {headlines.map((h) => {
          const effect = h.id ? effects.get(h.id) : undefined;
          return (
            <div key={h.id ?? h.title} style={{ minWidth: 0 }}>
              <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>
                {h.title}
              </span>
              <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                {' '}
                · <span title={h.time ? formatNyTitle(h.time) : undefined}>{displayTime(h.time)}</span>
                {h.about_symbol === true ? ` · about ${decision.symbol}`
                  : h.about_symbol === false ? ` · not about ${decision.symbol}` : ''}
                {h.new === false ? ' · seen before' : ''}
              </span>
              {effect ? (
                <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                  cited: &ldquo;{effect}&rdquo;
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </Section>
  );
}

function V4Thesis({ decision }: { decision: DecisionDetail }) {
  const pos = _v3Position(decision);
  const atEntry = pos?.checks_at_entry;
  const now = _v3Checks(decision);
  const session = decision.input_snapshot?.session as
    | { minutes_left?: number | null }
    | undefined;
  if (pos === null) return null;
  const rows: { name: string; at: string; current: string; adverse: boolean }[] = (
    ['trend_1h', 'trend_daily', 'regime_trend', 'momentum'] as const
  ).map((name) => {
    const at = atEntry?.[name] ?? '—';
    const cur = now?.[name] ?? '—';
    const adverse = checkTurnedAdverse(name, at, cur);
    return { name, at, current: cur, adverse };
  });
  // decision.v5: a fifth comparable check — the price against the
  // 1-hour EMA 20; adverse when below with price_vs_signal ≤ −0.5 ATR.
  if (pos.price_vs_ema20 != null) {
    const vsSignal = pos.price_vs_signal;
    const adverse =
      pos.price_vs_ema20 === 'below' &&
      vsSignal != null &&
      Number(vsSignal) <= -0.5;
    rows.push({
      name: 'Price vs EMA 20',
      at: '—',
      current: `${pos.price_vs_ema20}${vsSignal != null ? ` · ${signed(vsSignal)} ATR` : ''}`,
      adverse,
    });
  }
  const heldBits = [
    pos.held_session_hours != null
      ? `${Number(pos.held_session_hours).toFixed(1)} session hours`
      : null,
    pos.sessions_held != null ? `${pos.sessions_held} session${pos.sessions_held === 1 ? '' : 's'} held` : null,
    session?.minutes_left != null ? `${session.minutes_left} min left` : null,
  ].filter(Boolean);
  return (
    <Section
      label="Does the entry thesis still hold?"
      right={
        pos.thesis_holds === true
          ? 'yes — checked by code'
          : pos.thesis_holds === false
            ? 'no — 2+ checks turned adverse'
            : 'entry checks unknown'
      }
    >
      {heldBits.length > 0 && (
        <div
          style={{
            fontFamily: MONO,
            fontSize: 'var(--sa-text-sm)',
            color: 'var(--sa-text-muted)',
          }}
        >
          {heldBits.join(' · ')}
        </div>
      )}
      {atEntry && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
          {rows.map((r) => (
            <div
              key={r.name}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                fontSize: 'var(--sa-text-sm)',
                color: r.adverse ? 'var(--sa-negative-text)' : 'var(--sa-text-muted)',
              }}
            >
              <span>{r.name.replace(/_/g, ' ')}</span>
              <span style={{ fontFamily: MONO }}>
                at entry {r.at} → now {r.current}
              </span>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

function _v3Position(decision: DecisionDetail): V3Position | null {
  const pos = decision.input_snapshot?.position;
  return pos && typeof pos === 'object' ? (pos as V3Position) : null;
}

/** decision.v5 held: the Hold or exit block — confidence against the
 * neutral probability, the exit line and, in the last cycle, the
 * overnight line (design/screens/DecisionV5Held.dc.html). */
function V5HoldOrExit({ decision }: { decision: DecisionDetail }) {
  const pos = _v3Position(decision);
  if (pos === null || pos.neutral_probability == null) return null;
  const neutral = Number(pos.neutral_probability);
  const confidence = decision.model_output?.confidence ?? null;
  const session = decision.input_snapshot?.session as
    | { last_cycle?: boolean; minutes_left?: number | null }
    | undefined;
  const overnight = decision.input_snapshot?.overnight as
    | { gap_p90_atr?: number | null }
    | undefined;
  const gap = overnight?.gap_p90_atr != null ? Number(overnight.gap_p90_atr) : null;
  const stopAtr = pos.stop_distance_atr != null ? Number(pos.stop_distance_atr) : null;
  const lastCycle = session?.last_cycle === true;
  const exitLine = neutral - 0.03;
  const holdLine = neutral + 0.10;
  const rows: { label: string; value: string; sub?: string }[] = [
    {
      label: 'Exit below',
      value: exitLine.toFixed(2),
      sub: `neutral ${neutral.toFixed(2)} − 0.03`,
    },
    {
      label: 'Neutral probability',
      value: neutral.toFixed(2),
      sub: 'break-even between holding and selling',
    },
    {
      label: "Model's confidence",
      value: confidence !== null ? formatConfidence(confidence) : '—',
      sub:
        confidence !== null
          ? confidence < exitLine
            ? 'below the exit line'
            : lastCycle && confidence < holdLine && stopAtr !== null && gap !== null && stopAtr < gap
              ? 'below the overnight line'
              : 'holds'
          : undefined,
    },
  ];
  if (lastCycle) {
    rows.splice(1, 0, {
      label: 'Hold overnight from',
      value: holdLine.toFixed(2),
      sub: `neutral ${neutral.toFixed(2)} + 0.10${
        stopAtr !== null && gap !== null
          ? ` · stop ${stopAtr.toFixed(2)} ATR vs overnight gap p90 ${gap.toFixed(2)} ATR`
          : ''
      }`,
    });
  }
  return (
    <Section label="Hold or exit" right="from the neutral probability">
      <div
        style={{
          border: '1px solid var(--sa-border)',
          borderRadius: 'var(--sa-radius-md)',
          overflow: 'hidden',
        }}
      >
        {rows.map((row, i) => (
          <div
            key={row.label}
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              gap: 'var(--sa-space-3)',
              padding: '8px 12px',
              borderTop: i === 0 ? 'none' : '1px solid var(--sa-border)',
              fontSize: 'var(--sa-text-md)',
            }}
          >
            <span style={{ color: 'var(--sa-text-muted)' }}>{row.label}</span>
            <span style={{ textAlign: 'right', minWidth: 0 }}>
              <span style={{ fontFamily: MONO, color: 'var(--sa-text)' }}>{row.value}</span>
              {row.sub && (
                <span style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}>
                  {' '}
                  {row.sub}
                </span>
              )}
            </span>
          </div>
        ))}
      </div>
    </Section>
  );
}

function _factRow(label: string, value: string, sub?: string) {
  return (
    <div
      key={label}
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        gap: 'var(--sa-space-3)',
        padding: '8px 12px',
        fontSize: 'var(--sa-text-md)',
        borderBottom: '1px solid var(--sa-border)',
      }}
    >
      <span style={{ color: 'var(--sa-text-muted)' }}>{label}</span>
      <span style={{ textAlign: 'right', minWidth: 0, overflowWrap: 'anywhere' }}>
        <span style={{ fontFamily: MONO, color: 'var(--sa-text)' }}>{value}</span>
        {sub && (
          <span style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}>
            {' '}
            {sub}
          </span>
        )}
      </span>
    </div>
  );
}

function V3PositionFacts({ decision }: { decision: DecisionDetail }) {
  const pos = _v3Position(decision);
  if (pos === null) return null;
  const sd = pos.stop_distance_atr;
  const td = pos.target_distance_atr;
  return (
    <Section label="Position" right="facts the model was given">
      <div
        style={{
          border: '1px solid var(--sa-border)',
          borderRadius: 'var(--sa-radius-md)',
          overflow: 'hidden',
        }}
      >
        {_factRow(
          'R now',
          pos.r_now != null ? `${signed(pos.r_now)}R` : '—',
          pos.entry != null ? `entry ${pos.entry.toFixed(2)}` : undefined,
        )}
        {_factRow(
          'Current stop',
          pos.stop != null ? `${pos.stop.toFixed(2)}` : '—',
          sd != null ? `· ${sd.toFixed(2)} ATR below` : undefined,
        )}
        {_factRow(
          'Target',
          pos.target != null ? `${pos.target.toFixed(2)}` : '—',
          td != null ? `· ${td.toFixed(2)} ATR above` : undefined,
        )}
        {_factRow(
          'Neutral probability',
          pos.neutral_probability != null ? pos.neutral_probability.toFixed(2) : '—',
          sd != null && td != null
            ? `target before stop with no edge: ${sd.toFixed(2)} ÷ (${sd.toFixed(2)} + ${td.toFixed(2)})`
            : undefined,
        )}
        {pos.price_vs_signal != null &&
          _factRow(
            'Price since the signal bar',
            `${signed(pos.price_vs_signal)} ATR`,
            'negative is against the trade',
          )}
        {_factRow(
          'Levels to target',
          (pos.levels_to_target ?? [])
            .map(
              (lv) =>
                `${lv.id}${lv.distance_atr != null ? ` · ${lv.distance_atr.toFixed(2)} ATR` : ''}`,
            )
            .join('  ') || '—',
          `${(pos.levels_to_target ?? []).length} level${(pos.levels_to_target ?? []).length === 1 ? '' : 's'} between the price and the target`,
        )}
        {_factRow('Invalidation', pos.invalidation ?? 'unavailable', 'filled by code')}
        {pos.stop_level &&
          _factRow('Stop level', pos.stop_level, 'this cycle’s id of the stop’s level')}
        {pos.scheduled_exit && _factRow('Scheduled exit', pos.scheduled_exit)}
        {_factRow(
          'Entry thesis',
          pos.entry_thesis ?? 'unavailable',
          'what was believed at entry, not a fact',
        )}
      </div>
    </Section>
  );
}

function V3StopOptions({ decision }: { decision: DecisionDetail }) {
  const offered = _v2StopOptions(decision);
  const dropped = decision.options?.dropped_stop_options ?? [];
  if (offered.length === 0 && dropped.length === 0) return null;
  const chosen = decision.chosen_option;
  const rows = [...offered, ...dropped];
  return (
    <Section
      label="Stop options"
      right={`${offered.length} offered · ${dropped.length} not offered`}
    >
      <div
        style={{
          border: '1px solid var(--sa-border)',
          borderRadius: 'var(--sa-radius-md)',
          overflowX: 'auto', minWidth: 0,
        }}
      >
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--sa-border)' }}>
              {['', 'Stop · basis', 'Below price', 'Locked R'].map((h) => (
                <th
                  key={h}
                  style={{
                    textAlign: 'left',
                    padding: '8px 10px',
                    fontSize: 'var(--sa-text-sm)',
                    color: 'var(--sa-text-muted)',
                    fontWeight: 500,
                  }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((o) => {
              const notOffered = o.offered === false;
              return (
                <tr
                  key={o.id}
                  style={{
                    borderBottom: '1px solid var(--sa-border)',
                    opacity: notOffered ? 0.6 : 1,
                    background:
                      o.id === chosen ? 'var(--sa-accent-bg, rgba(127,184,255,0.10))' : undefined,
                  }}
                >
                  <td
                    style={{
                      padding: '8px 10px',
                      fontFamily: MONO,
                      fontWeight: 600,
                      color: o.id === chosen ? 'var(--sa-accent)' : 'var(--sa-text-2)',
                    }}
                  >
                    {o.id}
                  </td>
                  <td style={{ padding: '8px 10px', fontSize: 'var(--sa-text-sm)' }}>
                    <div style={{ fontFamily: MONO }}>{o.stop.toFixed(2)}</div>
                    <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-xs, 11px)' }}>
                      {o.basis}
                      {notOffered && o.drop_reason ? ` · not offered: ${o.drop_reason}` : ''}
                    </div>
                  </td>
                  <td style={{ padding: '8px 10px', fontFamily: MONO, fontSize: 'var(--sa-text-sm)', textAlign: 'right' }}>
                    {o.distance_atr != null ? o.distance_atr.toFixed(2) : '—'}
                  </td>
                  <td style={{ padding: '8px 10px', fontFamily: MONO, fontSize: 'var(--sa-text-sm)', textAlign: 'right' }}>
                    {o.locked_r != null ? `${signed(o.locked_r)}R` : '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {muted(
        'Built by code. "Locks profit" only for a stop above the entry. Not offered: moves under the minimum ATR, break-even before the minimum R, and stops too close to the price.',
      )}
    </Section>
  );
}

function V3Reasoning({ decision }: { decision: DecisionDetail }) {
  const output = decision.model_output;
  const validation = decision.validation_result;
  const conflicts = (output?.conflicts ?? []).filter(
    (c): c is string => typeof c === 'string',
  );
  const flags = output?.data_flags ?? [];
  const thesis = output?.thesis ?? '';
  const news = (output?.news_used ?? []).filter(
    (n): n is string => typeof n === 'string',
  );
  const levelsUsed = output?.levels_used ?? [];
  const written = validation?.code_written;
  return (
    <Section label="Agent reasoning" right="conflicts first, then the decision">
      {conflicts.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
          {conflicts.map((conflict) => (
            <Chip key={conflict} variant="warning" wrap>
              Conflict: {conflict}
            </Chip>
          ))}
        </div>
      )}
      {flags.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
          {flags.map((flag) => (
            <Chip key={flag} variant="negative">
              Data flag: {flag.replace(/_/g, ' ')}
            </Chip>
          ))}
        </div>
      )}
      {thesis ? (
        <p
          style={{
            margin: 0,
            fontSize: 'var(--sa-text-md)',
            color: 'var(--sa-text-2)',
            lineHeight: 1.55,
          }}
        >
          {thesis}
        </p>
      ) : (
        muted('No model reasoning recorded.')
      )}
      {written && (
        <div
          style={{
            fontSize: 'var(--sa-text-sm)',
            color: 'var(--sa-text-muted)',
            fontFamily: MONO,
          }}
        >
          Written by code: {written}
        </div>
      )}
      {levelsUsed.length > 0 && (
        <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          Levels used:{' '}
          <span style={{ color: 'var(--sa-text-2)' }}>{levelsUsed.join(' · ')}</span>
          <span style={{ color: 'var(--sa-text-muted)' }}>
            {' '}
            · {flags.length === 0 ? 'No data flags' : `${flags.length} data flag${flags.length === 1 ? '' : 's'}`}
          </span>
        </div>
      )}
      {news.length > 0 && (
        <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          News used: <span style={{ color: 'var(--sa-text-2)' }}>{news.join(' · ')}</span>
        </div>
      )}
    </Section>
  );
}

function V2Explanation({ decision }: { decision: DecisionDetail }) {
  const check = decision.explanation_check;
  if (check === undefined || check === null) return null;
  return check.passed ? null : (
    <div
      style={{
        border: '1px solid var(--sa-warning)',
        borderRadius: 'var(--sa-radius-md)',
        padding: '8px 10px',
        fontSize: 'var(--sa-text-sm)',
        color: 'var(--sa-warning-text)',
      }}
    >
      Explanation check failed
      {check.bad_ids.length > 0 && ` — unknown ids ${check.bad_ids.join(', ')}`}
      {check.bad_numbers.length > 0 &&
        ` — unsupported numbers ${check.bad_numbers.join(', ')}`}
      {' '}— the decision stands.
    </div>
  );
}

// ---------------------------------------------------------------------------
// What happened — one block on every version (design/DESIGN.md → Decision
// drawers): final action, changes by code, warnings, order status, verdict.

function _whRow(label: string, children: React.ReactNode, first = false) {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '118px minmax(0, 1fr)',
        gap: 'var(--sa-space-3)',
        padding: '8px 12px',
        borderTop: first ? 'none' : '1px solid var(--sa-border)',
        fontSize: 'var(--sa-text-md)',
        alignItems: 'start',
      }}
    >
      <span style={{ color: 'var(--sa-text-muted)' }}>{label}</span>
      <span style={{ minWidth: 0, lineHeight: 1.5 }}>{children}</span>
    </div>
  );
}

/** The confirm flow inside What happened: one "Confirm <draft>" button,
 * "Change…" revealing the two other labels (design: the draft is the
 * primary action). */
function VerdictCell({
  decision,
  onResolved,
}: {
  decision: DecisionDetail;
  onResolved: () => Promise<void>;
}) {
  const verdict = decision.verdict;
  const [changing, setChanging] = useState(false);
  const [pending, setPending] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const pick = async (value: VerdictValue) => {
    if (!verdict || pending) return;
    setPending(true);
    setSaveError(null);
    try {
      await postVerdict(verdict.id, value);
      await onResolved();
    } catch {
      setSaveError('The verdict could not be saved or refreshed. Retry your choice.');
    } finally {
      setPending(false);
    }
  };

  if (verdict) {
    const others = (
      ['good_call', 'bad_call', 'unclear'] as VerdictValue[]
    ).filter((v) => v !== verdict.verdict);
    return (
      <span style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 'var(--sa-space-2)' }}>
        <Chip variant={verdictVariant(verdict.verdict)}>
          {verdict.source === 'draft' ? `draft · ${verdictLabel(verdict.verdict)}` : verdictLabel(verdict.verdict)}
        </Chip>
        {verdict.reason && (
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{verdict.reason}</span>
        )}
        {verdict.source === 'draft' && !changing && (
          <>
            <DialogButton
              variant="accent"
              disabled={pending}
              onClick={() => void pick(verdict.verdict)}
            >
              Confirm {verdictLabel(verdict.verdict).toLowerCase()}
            </DialogButton>
            <DialogButton variant="cancel" disabled={pending} onClick={() => setChanging(true)}>
              Change…
            </DialogButton>
          </>
        )}
        {changing &&
          others.map((v) => (
            <DialogButton key={v} variant="cancel" disabled={pending} onClick={() => void pick(v)}>
              {verdictLabel(v)}
            </DialogButton>
          ))}
        {pending && <span role="status">Saving verdict…</span>}
        {saveError && <span role="alert">{saveError}</span>}
      </span>
    );
  }
  if (decision.verdict_eligible) {
    return (
      <span style={{ color: 'var(--sa-text-muted)' }}>
        pending · a draft is written after 5 sessions
        {decision.verdict_due ? ` (${dueDayLabel(decision.verdict_due)})` : ''}
        , then you confirm or change it here
      </span>
    );
  }
  return (
    <span style={{ color: 'var(--sa-text-muted)' }}>
      none ·{' '}
      {decision.context === 'held'
        ? 'verdicts are written for entries and skips, not for hold, exit or tighten'
        : decision.final_action === 'no_call'
          ? 'the model was not called this cycle'
          : 'this decision gets no verdict'}
    </span>
  );
}

function WhatHappened({
  decision,
  backtest,
  onVerdictResolved,
}: {
  decision: DecisionDetail;
  backtest: boolean;
  onVerdictResolved: () => Promise<void>;
}) {
  const ask = useAsk();
  const location = useLocation();
  const output = decision.model_output;
  const validation = decision.validation_result as {
    changed_by_code?: boolean;
    model_action?: string;
    reason?: string | null;
    note?: string | null;
  } | null;
  const gates = decision.gates_result as { blocked_by?: string | null } | null;
  const changed = Boolean(validation?.changed_by_code);
  const option =
    decision.chosen_option ?? output?.option_id ?? output?.best_option_id ?? null;
  const changeReason = validation?.reason ?? gates?.blocked_by ?? null;
  // Sizing reductions are a final-action detail, not a code change
  // (design: "size reduced 5 → 4 sh by max position 20%").
  const sizing = (decision.gates_result as GatesResult | null)?.sizing;
  const reduced =
    (sizing?.limited_by ?? []).length > 0
      ? `size reduced to ${sizing?.shares ?? '—'} sh by ${(sizing?.limited_by ?? []).join(' and ').replace(/_/g, ' ')}`
      : null;
  const warnings = (decision.warnings ?? []).filter((w) => w !== 'changed_by_code');
  const finalParts = [
    `${actionText(decision.final_action).label}${option && decision.final_action !== 'no_call' ? ` ${option}` : ''}`,
    output
      ? changed && validation?.model_action
        ? `the model said ${validation.model_action.toUpperCase().replace('_', ' ')}`
        : 'as the model chose'
      : 'the model was not asked this cycle',
    reduced ??
      (decision.trade?.qty && decision.trade.entry
        ? `${decision.trade.qty} sh @ ${Number(decision.trade.entry).toFixed(2)}`
        : null),
  ].filter(Boolean);

  return (
    <Section label="What happened" right="final action, code, order, verdict">
      <div
        style={{
          border: '1px solid var(--sa-border)',
          borderRadius: 'var(--sa-radius-md)',
          overflow: 'hidden',
          background: 'var(--sa-raised)',
        }}
      >
        {_whRow('Final action', finalParts.join(' · '), true)}
        {_whRow(
          'Changed by code',
          changed ? (
            <span style={{ display: 'inline-flex', flexWrap: 'wrap', alignItems: 'center', gap: '6px' }}>
              <Chip variant="warning">{changeReason ?? 'changed'}</Chip>
              {validation?.note && (
                <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                  {validation.note}
                </span>
              )}
            </span>
          ) : (
            <span style={{ color: 'var(--sa-text-muted)' }}>
              nothing · the final action is the model's
            </span>
          ),
        )}
        {_whRow(
          'Warnings',
          warnings.length > 0 ? (
            <span style={{ display: 'inline-flex', flexWrap: 'wrap', gap: '6px' }}>
              {warnings.map((w) => (
                <Chip key={w} variant="warning">{w}</Chip>
              ))}
            </span>
          ) : (
            <span style={{ color: 'var(--sa-text-muted)' }}>none</span>
          ),
        )}
        {_whRow('Order', orderStatusText(decision, backtest))}
        {decision.stop_missing && (
          _whRow(
            'Stop at Alpaca',
            <span style={{ color: 'var(--sa-negative-text)' }}>
              none since {displayTime(decision.stop_missing.since)} ·{' '}
              {elapsedHm(decision.stop_missing.since)}
              {decision.stop_missing.reason
                ? ` — ${decision.stop_missing.reason}`
                : ''}
            </span>,
          )
        )}
        {_whRow(
          'Verdict',
          <VerdictCell decision={decision} onResolved={onVerdictResolved} />,
        )}
      </div>
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <DialogButton
          variant="cancel"
          title="Opens Ask the agent with this decision as its context (Ctrl+J)"
          onClick={() =>
            ask.openDrawer({
              screen: 'decision',
              label: `${decision.symbol} decision`,
              record_ref: `decision:${decision.id}`,
              summary: `${decision.symbol} ${decision.final_action}`,
              route: location.pathname,
            })
          }
        >
          Ask about this decision
        </DialogButton>
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// level tiles + outcome

function LevelTile({
  label,
  value,
  accent,
}: {
  label: string;
  value: string | null;
  accent?: string;
}) {
  return (
    <div
      style={{
        padding: '10px',
        border: `1px ${value ? 'solid var(--sa-border)' : 'dashed var(--sa-border-strong)'}`,
        borderRadius: 'var(--sa-radius-md)',
        display: 'flex',
        flexDirection: 'column',
        gap: '4px',
        minWidth: 0,
      }}
    >
      <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
        {label}
      </span>
      <span
        style={{
          fontFamily: 'var(--sa-font-mono)',
          color: value ? (accent ?? 'var(--sa-text)') : 'var(--sa-text-muted)',
          fontSize: 'var(--sa-text-md)',
        }}
      >
        {value !== null && ['Entry', 'Entry limit', 'Stop', 'Target'].includes(label)
          ? Number(value).toFixed(2)
          : value ?? '—'}
      </span>
    </div>
  );
}

function OutcomeTile({ label, value, sub, pending }: {
  label: string; value: string; sub?: string | null; pending?: boolean;
}) {
  return (
    <div
      style={{
        padding: '10px',
        border: `1px ${pending ? 'dashed var(--sa-border-strong)' : 'solid var(--sa-border)'}`,
        borderRadius: 'var(--sa-radius-md)',
        display: 'flex',
        flexDirection: 'column',
        gap: '4px',
      }}
    >
      <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{label}</span>
      <span
        style={{
          fontFamily: 'var(--sa-font-mono)',
          fontSize: 'var(--sa-text-md)',
          color: pending ? 'var(--sa-text-muted)' : 'var(--sa-text)',
        }}
      >
        {value}
      </span>
      {sub && (
        <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-xs)', color: 'var(--sa-text-muted)' }}>
          {sub}
        </span>
      )}
    </div>
  );
}

/** A skip is judged by what its options did (design: option touches). */
function OptionTouches({ decision }: { decision: DecisionDetail }) {
  const touches = decision.outcome?.option_touches ?? null;
  const options = _v2Options(decision);
  const chosen = decision.chosen_option ?? decision.model_output?.best_option_id;
  const ids = touches
    ? Object.keys(touches)
    : options.map((o) => o.id);
  if (ids.length === 0) {
    return muted('No options were built — there is nothing to measure.');
  }
  return (
    <>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
          gap: 'var(--sa-space-2)',
        }}
      >
        {ids.map((oid) => {
          const option = options.find((o) => o.id === oid);
          const touch = touches?.[oid];
          const pending = touch === null || touch === undefined;
          return (
            <OutcomeTile
              key={oid}
              label={`${oid}${option ? ` · ${option.rr.toFixed(1)}R` : ''}${oid === chosen ? ' · chosen' : ''}`}
              value={pending ? 'pending' : touch === 'not_filled' ? 'not filled' : touch}
              pending={pending}
              sub={pending ? 'neither yet' : touch === 'stop' ? 'stop first' : touch === 'target' ? 'target first' : undefined}
            />
          );
        })}
      </div>
      {touches === null && muted('Option touches fill once the 5-session window closes.')}
    </>
  );
}

/** A hold or exit is judged by what the position did afterwards, and the
 * next open against the exit when it closed (design: held outcome). */
function HeldAftermath({ decision }: { decision: DecisionDetail }) {
  const after = decision.held_after;
  if (after === null || after === undefined) {
    return muted('The position is still open — its outcome fills when it closes.');
  }
  const r = after.r !== null ? ` · ${signed(Number(after.r))}R` : '';
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
        gap: 'var(--sa-space-2)',
      }}
    >
      <OutcomeTile
        label="Exit"
        value={`${after.exit_price ? Number(after.exit_price).toFixed(2) : '—'}${r}`}
        sub={after.closed_at ? displayDayTime(after.closed_at) : null}
      />
      <OutcomeTile
        label="Best after exit"
        value={after.after_exit_best_price ? Number(after.after_exit_best_price).toFixed(2) : 'pending'}
        pending={!after.after_exit_best_price}
        sub={after.after_exit_best_at ? displayDayTime(after.after_exit_best_at) : null}
      />
      <OutcomeTile
        label="5 sessions after"
        value={after.after_exit_close_price ? Number(after.after_exit_close_price).toFixed(2) : 'pending'}
        pending={!after.after_exit_close_price}
        sub={after.after_exit_status && after.after_exit_status !== 'done' ? after.after_exit_status : null}
      />
    </div>
  );
}

function OutcomeSection({ decision }: { decision: DecisionDetail }) {
  const outcome = decision.outcome;
  const held = decision.context === 'held';
  const skip = !held && ['skip', 'blocked', 'no_call'].includes(decision.final_action)
    && (decision.model_output !== null || decision.final_action === 'skip');
  const tiles: {
    label: string;
    price: string | null;
    r: string | null;
    at: string | null;
  }[] = [
    {
      label: '1h',
      price: outcome?.price_1h ?? null,
      r: outcome?.r_1h ?? null,
      at: outcome?.price_1h_at ?? null,
    },
    {
      label: '1 session',
      price: outcome?.price_1session ?? null,
      r: outcome?.r_1session ?? null,
      at: outcome?.price_1session_at ?? null,
    },
    {
      label: '5 sessions',
      price: outcome?.price_5sessions ?? null,
      r: outcome?.r_5sessions ?? null,
      at: outcome?.price_5sessions_at ?? null,
    },
  ];
  const touchLabel = firstTouchLabel(outcome?.first_touch);
  const soFar: string[] = [];
  if (outcome?.best_r) soFar.push(`best ${signed(Number(outcome.best_r))}R`);
  else if (outcome?.best_price) soFar.push(`best ${outcome.best_price}`);
  if (outcome?.worst_r) soFar.push(`worst ${signed(Number(outcome.worst_r))}R`);
  else if (outcome?.worst_price) soFar.push(`worst ${outcome.worst_price}`);
  if (touchLabel) soFar.push(`first touch: ${touchLabel}`);

  const right =
    held ? 'after this decision'
    : skip ? 'option touches · 5 sessions'
    : decision.next_outcome_due
      ? <span title={formatNyTitle(decision.next_outcome_due)}>next result {displayDayTime(decision.next_outcome_due)}</span>
      : outcome === null ? 'not recorded'
        : tiles.every((tile) => tile.price !== null) ? 'all results in' : 'results pending';

  return (
    <Section label="Outcome" right={right}>
      {held ? (
        <HeldAftermath decision={decision} />
      ) : skip ? (
        <OptionTouches decision={decision} />
      ) : outcome === null ? (
        muted('No outcome row stored for this decision.')
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
            gap: 'var(--sa-space-2)',
          }}
        >
          {tiles.map((tile) => (
            <OutcomeTile
              key={tile.label}
              label={tile.label}
              value={
                tile.price
                  ? `${Number(tile.price).toFixed(2)}${tile.r ? ` · ${signed(Number(tile.r))}R` : ''}`
                  : 'pending'
              }
              pending={!tile.price}
              sub={tile.at ? `bar ${displayTime(tile.at)}` : null}
            />
          ))}
        </div>
      )}
      {soFar.length > 0 && (
        <div
          style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}
        >
          So far: {soFar.join(' · ')}
        </div>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// drawer — design/screens/Decision.dc.html: 560px, docked right, over a scrim


function DrawerShell({
  label,
  onClose,
  children,
}: {
  label: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  // An open Ask drawer docks at the right edge — this drawer docks to
  // its left so both stay visible and usable (design: side by side).
  const ask = useAsk();
  const askDocked = useMediaQuery('(min-width: 1280px)');
  const inset = ask.open && askDocked ? '0 440px 0 0' : '0';
  const overlayRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; });
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    const siblings = Array.from(document.body.children)
      .filter((node): node is HTMLElement => node instanceof HTMLElement && node !== overlayRef.current)
      .map((node) => ({ node, inert: node.inert }));
    const overflow = document.body.style.overflow;
    siblings.forEach(({ node }) => { node.inert = true; });
    document.body.style.overflow = 'hidden';
    panel?.focus();
    const observer = new MutationObserver(() => {
      if (panel?.isConnected && !panel.contains(document.activeElement)) panel.focus();
    });
    if (panel) observer.observe(panel, { childList: true, subtree: true });
    const onFocus = (e: FocusEvent) => {
      if (e.target instanceof Node && !panel?.contains(e.target)) panel?.focus();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeRef.current();
      }
      if (e.key !== 'Tab' || !panel) return;
      const controls = Array.from(panel.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      )).filter((node) => node.getClientRects().length > 0);
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (!first || (e.shiftKey && (document.activeElement === first || document.activeElement === panel))) {
        e.preventDefault();
        (last ?? panel).focus();
      } else if (!e.shiftKey && (document.activeElement === last || document.activeElement === panel)) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('focusin', onFocus);
    return () => {
      observer.disconnect();
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('focusin', onFocus);
      siblings.forEach(({ node, inert }) => { node.inert = inert; });
      document.body.style.overflow = overflow;
      if (previous?.isConnected) previous.focus();
    };
  }, []);

  return createPortal(
    <div ref={overlayRef} style={{ position: 'fixed', inset, zIndex: 100 }}>
      <div
        aria-hidden
        onClick={onClose}
        style={{
          position: 'absolute',
          inset: 0,
          background: 'rgba(0,0,0,0.6)',
          animation: 'sa-fade-in 160ms ease-out',
        }}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        style={{
          position: 'absolute',
          top: 0,
          right: 0,
          bottom: 0,
          width: 'var(--sa-drawer-width)',
          maxWidth: '100%',
          background: 'var(--sa-chrome)',
          borderLeft: '1px solid var(--sa-border)',
          color: 'var(--sa-text)',
          display: 'flex',
          flexDirection: 'column',
          animation: 'sa-drawer-in 160ms ease-out',
        }}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}

// ---------------------------------------------------------------------------
// page

export default function Decision() {
  const { id } = useParams<{ id: string }>();
  const [searchParams] = useSearchParams();
  // A backtest decision lives in the run's own database: ?bt=<run>&s=<slug>.
  const btRun = searchParams.get('bt');
  const btSlug = searchParams.get('s');
  const backtest = searchParams.has('bt') || searchParams.has('s');
  const fetchDecision = useCallback(
    (decisionId: number) => {
      if (!Number.isSafeInteger(decisionId) || decisionId < 1 ||
          (backtest && (!btRun || !btSlug || !Number.isSafeInteger(Number(btRun)) || Number(btRun) < 1)))
        return Promise.reject(new ApiError(422, 'Invalid decision link'));
      return backtest
        ? getBacktestDecision(Number(btRun), btSlug ?? '', decisionId)
        : getDecision(decisionId);
    },
    [btRun, btSlug, backtest],
  );
  const location = useLocation();
  const navigate = useNavigate();
  const ask = useAsk();
  const tilesNarrow = useMediaQuery('(max-width: 820px)');
  const requestKey = `${id}:${btRun ?? ''}:${btSlug ?? ''}`;
  const [result, setResult] = useState<{ key: string; decision: DecisionDetail | null; error: string | null; loading: boolean }>({
    key: requestKey, decision: null, error: null, loading: true,
  });
  const [retry, setRetry] = useState(0);
  const requestSequence = useRef(0);
  const activeKey = useRef<string | null>(null);
  useEffect(() => {
    activeKey.current = requestKey;
    return () => { activeKey.current = null; };
  }, [requestKey]);
  const decision = result.key === requestKey ? result.decision : null;
  const error = result.key === requestKey ? result.error : null;

  const background = (
    location.state as { backgroundLocation?: Location } | null
  )?.backgroundLocation;
  const close = useCallback(() => {
    navigate(
      background
        ? `${background.pathname}${background.search}${background.hash}`
        : '/',
    );
  }, [navigate, background]);

  const refresh = useCallback(async () => {
    if (activeKey.current !== requestKey) return;
    const sequence = ++requestSequence.current;
    setResult((previous) => ({
      key: requestKey,
      decision: previous.key === requestKey ? previous.decision : null,
      error: previous.key === requestKey && previous.decision ? previous.error : null,
      loading: true,
    }));
    try {
      const next = await fetchDecision(Number(id));
      if (sequence === requestSequence.current)
        setResult({ key: requestKey, decision: next, error: null, loading: false });
    } catch (e) {
      if (sequence !== requestSequence.current) return;
      setResult((previous) => ({
        key: requestKey,
        decision: previous.key === requestKey ? previous.decision : null,
        error: e instanceof ApiError && e.status === 404 ? 'not_found'
          : e instanceof ApiError && e.status === 422 ? 'invalid_request' : 'error',
        loading: false,
      }));
      throw e;
    }
  }, [fetchDecision, id, requestKey]);
  useEffect(() => {
    void refresh().catch(() => undefined);
    return () => { requestSequence.current += 1; };
  }, [refresh, retry]);
  useLiveUpdates(backtest ? ['backtests'] : ['decisions', 'orders', 'positions', 'approvals', 'reviews'], () => {
    void refresh().catch(() => undefined);
  });
  const stateHeader = (
    <div style={{ padding: 'var(--sa-space-4)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 'var(--sa-space-3)' }}>
      <h1 style={{ margin: 0, fontSize: 'var(--sa-text-xl)' }}>Decision detail</h1>
      <div style={{ display: 'flex', gap: 'var(--sa-space-3)' }}>
        {result.decision && (
          <DialogButton
            variant="cancel"
            onClick={() =>
              ask.openDrawer({
                screen: 'decision',
                label: `${result.decision!.symbol} decision`,
                record_ref: `decision:${result.decision!.id}`,
                summary: `${result.decision!.symbol} ${result.decision!.final_action}`,
                route: location.pathname,
              })
            }
          >
            Ask about this
          </DialogButton>
        )}
        <DialogButton variant="cancel" onClick={close}>Close</DialogButton>
      </div>
    </div>
  );

  if (error === 'not_found') {
    // The record's not-found page (design: Errors → Not found · a record),
    // inside the drawer — the page behind it keeps working.
    return (
      <DrawerShell label="Decision detail" onClose={close}>
        {stateHeader}
        <RecordNotFound
          what={`Decision ${id} doesn't exist`}
          why="Decisions are never deleted within a run."
          searchTo="/inspector"
          searchLabel="Search the LLM inspector"
        />
      </DrawerShell>
    );
  }
  if (error && !decision) {
    return (
      <DrawerShell label="Decision detail" onClose={close}>
        {stateHeader}
        <div role="alert"
          style={{
            padding: 'var(--sa-space-5) var(--sa-space-6)',
            color: 'var(--sa-text-muted)',
          }}
        >
          <p>{error === 'invalid_request' ? 'The decision link is invalid.' : 'The decision could not be loaded.'}</p>
          {error !== 'invalid_request' && <DialogButton variant="cancel" onClick={() => setRetry((value) => value + 1)}>Retry</DialogButton>}
        </div>
      </DrawerShell>
    );
  }
  if (!decision) {
    return (
      <DrawerShell label="Decision detail" onClose={close}>
        {stateHeader}
        <div aria-busy="true" aria-label="Loading decision" style={{ padding: 'var(--sa-space-6)' }}>
          <div className="sa-loading">
            <SkeletonLines />
          </div>
        </div>
      </DrawerShell>
    );
  }

  const snapshot = (decision.input_snapshot ?? {}) as Record<string, unknown>;
  const regime = snapshot.regime;
  const validation = decision.validation_result;
  const rules = decision.rules_result;
  const rulesPassed = rules
    ? `${rules.checks.filter((c) => c.passed).length} of ${rules.checks.length}`
    : null;
  const chip = actionText(decision.final_action);
  const badge = actionBadgeStyle(decision.final_action);
  const trade = decision.trade;
  const output = decision.model_output;
  const entryOrder = decision.orders?.find((o) => o.leg === 'entry');
  const hasOrder = Boolean(entryOrder);
  const total =
    trade?.qty && trade.entry ? trade.qty * Number(trade.entry) : null;
  const eurRate = decision.eur_usd_rate ? Number(decision.eur_usd_rate) : null;
  const eur = total !== null && eurRate ? total / eurRate : null;
  const confidence = output?.confidence ?? null;
  const held = decision.context === 'held';
  const position = _v3Position(decision);
  const posRNow = position?.r_now ?? num(
    held
      ? (decision.input_snapshot?.position as { r_so_far?: unknown } | undefined)?.r_so_far
      : null,
  );
  const selected = _v2Options(decision).find((option) =>
    option.id === (output?.option_id ?? output?.best_option_id ?? decision.chosen_option));
  const levelTiles = (
    <div style={{ display: 'grid', gridTemplateColumns: tilesNarrow ? 'repeat(2, minmax(0, 1fr))' : 'repeat(4, minmax(0, 1fr))',
      gap: 'var(--sa-space-2)', padding: 'var(--sa-space-4) 0' }}>
      <LevelTile label={!held && decision.prompt_version !== 'decision.v1' ? 'Entry limit' : 'Entry'} value={trade?.entry ?? null} />
      <LevelTile label="Stop" value={trade?.stop ?? null} accent="var(--sa-negative-text)" />
      <LevelTile label="Target" value={trade?.target ?? null} accent="var(--sa-positive)" />
      {decision.prompt_version === 'decision.v1' ? (
        <LevelTile label="Invalidation · 1h close" value={output?.invalidation ?? null} />
      ) : held ? (
        <LevelTile label="R now" value={posRNow !== null ? `${signed(posRNow)}R` : null} />
      ) : <LevelTile label={decision.prompt_version === 'decision.v2' ? 'R/R' : 'R/R · break-even'}
        value={selected ? `${selected.rr.toFixed(2)}${selected.breakeven_confidence != null ? ` · ${selected.breakeven_confidence.toFixed(2)}` : ''}` : null} />}
    </div>
  );

  return (
    <DrawerShell label={`${decision.symbol} decision`} onClose={close}>
        {/* header */}
        <div
          style={{
            padding: 'var(--sa-space-5) var(--sa-space-6) var(--sa-space-4)',
            borderBottom: '1px solid var(--sa-border)',
            display: 'flex',
            justifyContent: 'space-between',
            gap: 'var(--sa-space-3)',
          }}
        >
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: '6px',
              minWidth: 0,
            }}
          >
            <div
              title={formatNyTitle(decision.cycle_time)}
              style={{
                fontFamily: 'var(--sa-font-mono)',
                fontSize: 'var(--sa-text-sm)',
                color: 'var(--sa-text-muted)',
              }}
            >
              {formatAmsDateTime(decision.cycle_time)} cycle ·{' '}
              {decision.model ?? '—'} · {decision.prompt_version ?? '—'}
            </div>
            <h1
              style={{
                margin: 0,
                fontSize: 'var(--sa-text-xl)',
                fontWeight: 600,
              }}
            >
              {decision.symbol} decision
            </h1>
            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                alignItems: 'center',
                gap: 'var(--sa-space-2)',
              }}
            >
              <RegimeChip regime={regime} />
              {backtest && <Chip variant="info">Backtest · no broker order</Chip>}
              <span
                style={{
                  fontSize: 'var(--sa-text-sm)',
                  color: 'var(--sa-text-muted)',
                }}
              >
                {contextLabel(decision.context)}
                {rulesPassed ? ` · pre-filter ${rulesPassed} passed` : ''}
              </span>
            </div>
          </div>
          <button
            type="button"
            onClick={close}
            aria-label="Close"
            title="Close"
            style={{
              width: 'var(--sa-control-height)',
              height: 'var(--sa-control-height)',
              flex: '0 0 var(--sa-control-height)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              borderRadius: 'var(--sa-radius-md)',
              border: '1px solid var(--sa-border-strong)',
              background: 'transparent',
              color: 'var(--sa-text-muted)',
              cursor: 'pointer',
              padding: 0,
            }}
          >
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <line x1="6" y1="6" x2="18" y2="18" />
              <line x1="18" y1="6" x2="6" y2="18" />
            </svg>
          </button>
        </div>

        {/* body — scrolls between the pinned header and footer */}
        <div
          aria-busy={result.loading}
          style={{
            padding: '0 var(--sa-space-6) var(--sa-space-6)',
            display: 'flex',
            flexDirection: 'column',
            flex: 1,
            minHeight: 0,
            overflowY: 'auto',
          }}
        >
          {/* action row */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--sa-space-4)',
              padding: 'var(--sa-space-5) 0 var(--sa-space-4)',
            }}
          >
            <div
              style={{
                minWidth: '88px',
                height: '56px',
                padding: '0 16px',
                borderRadius: 'var(--sa-radius-md)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontFamily: 'var(--sa-font-mono)',
                fontWeight: 600,
                fontSize: 'var(--sa-text-xl)',
                background: badge.bg,
                color: badge.color,
                whiteSpace: 'nowrap',
              }}
            >
              {chip.label}
            </div>
            <div
              style={{
                flex: 1,
                minWidth: 0,
                display: 'flex',
                flexDirection: 'column',
                gap: '6px',
              }}
            >
              {trade && trade.qty && trade.entry ? (
                <div
                  style={{
                    fontFamily: 'var(--sa-font-mono)',
                    fontSize: 'var(--sa-text-md)',
                  }}
                >
                  {trade.qty} sh @ {Number(trade.entry).toFixed(2)}
                  {hasOrder ? ' limit' : ''}
                  {validation?.changed_by_code ? ' · changed by code' : ''}
                  {total !== null && (
                    <>
                      {' '}
                      ≈ ${Math.round(total).toLocaleString('en-US')}
                      {eur !== null && (
                        <span
                          style={{
                            color: 'var(--sa-text-muted)',
                            fontSize: 'var(--sa-text-sm)',
                          }}
                        >
                          {' '}
                          €{Math.round(eur).toLocaleString('en-US')}
                        </span>
                      )}
                    </>
                  )}
                </div>
              ) : (
                <div
                  style={{
                    fontSize: 'var(--sa-text-md)',
                    color: 'var(--sa-text-muted)',
                  }}
                >
                  {validation?.changed_by_code
                    ? `model said ${validation.model_action?.replace('_', ' ')} — code changed it`
                    : decision.final_action === 'no_call'
                      ? 'the model was not asked this cycle'
                      : actionLabel(decision.final_action)}
                </div>
              )}
              {confidence !== null && (
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 'var(--sa-space-2)',
                    fontSize: 'var(--sa-text-sm)',
                    color: 'var(--sa-text-muted)',
                  }}
                >
                  <span
                    title={
                      held
                        ? 'Held: starts at the neutral probability and moves only for named facts. Not gated; kept out of entry calibration.'
                        : _isV4Plus(decision.prompt_version)
                          ? 'Enter: the chosen option reaching its target before its stop. On a skip: the best option\u2019s chance (best_option_id).'
                          : undefined
                    }
                  >
                    confidence
                    {held
                      ? ` · target before ${output?.action === 'tighten_stop' ? 'new' : 'current'} stop`
                      : _isV4Plus(decision.prompt_version)
                        ? output?.action === 'skip'
                          ? ` · best ${output.best_option_id ?? '—'}`
                          : ` · option ${output?.option_id ?? '—'}`
                        : ''}
                  </span>
                  <div
                    style={{
                      flex: 1,
                      maxWidth: '140px',
                      height: '4px',
                      borderRadius: '2px',
                      background: 'var(--sa-raised)',
                      overflow: 'hidden',
                    }}
                  >
                    <div
                      style={{
                        width: `${Math.round(confidence * 100)}%`,
                        height: '100%',
                        background: 'var(--sa-accent)',
                      }}
                    />
                  </div>
                  <span
                    style={{
                      fontFamily: 'var(--sa-font-mono)',
                      color: 'var(--sa-text-2)',
                    }}
                  >
                    {formatConfidence(confidence)}
                  </span>
                </div>
              )}
            </div>
          </div>

          {error && <div role="alert">
            The decision could not be refreshed. Showing stale data.{' '}
            <DialogButton variant="cancel" onClick={() => setRetry((value) => value + 1)}>Retry</DialogButton>
          </div>}
          {/* One order on every version (design/DESIGN.md → Decision
              drawers): action · tiles · What happened · the version's
              blocks · reasoning · gates · levels (folded) · outcome. */}
          {levelTiles}
          <WhatHappened
            key={`wh-${requestKey}`}
            decision={decision}
            backtest={backtest}
            onVerdictResolved={refresh}
          />
          {_isV4Plus(decision.prompt_version) ? (
            <>
              {held ? (
                <>
                  {decision.prompt_version === 'decision.v5' && (
                    <V5HoldOrExit decision={decision} />
                  )}
                  <V3PositionFacts decision={decision} />
                  <V4Thesis decision={decision} />
                </>
              ) : (
                <>
                  <V4PreviousDecision decision={decision} />
                  <V3Checks decision={decision} />
                </>
              )}
              {decision.context === 'candidate' ? (
                <V3OptionsTable decision={decision} />
              ) : (
                <V3StopOptions decision={decision} />
              )}
              {!held && <SelectionBlock decision={decision} />}
              <V4Reasoning decision={decision} />
              <V4Headlines decision={decision} />
              <V2Explanation decision={decision} />
              <SafetyGates decision={decision} />
            </>
          ) : decision.prompt_version === 'decision.v3' ? (
            <>
              {held ? (
                <>
                  <V3PositionFacts decision={decision} />
                  <V3StopOptions decision={decision} />
                </>
              ) : (
                <>
                  <V3Checks decision={decision} />
                  <V3OptionsTable decision={decision} />
                  <SelectionBlock decision={decision} />
                </>
              )}
              <V3Reasoning decision={decision} />
              <V2Explanation decision={decision} />
              <SafetyGates decision={decision} />
            </>
          ) : (
            <>
              {decision.prompt_version === 'decision.v2' ? (
                <>
                  <V2OptionsTable decision={decision} />
                  <SelectionBlock decision={decision} />
                </>
              ) : (
                <SignalStack decision={decision} />
              )}
              <Reasoning decision={decision} />
              {decision.prompt_version === 'decision.v2' && (
                <V2Explanation decision={decision} />
              )}
              <SafetyGates decision={decision} />
            </>
          )}
          <V2Levels decision={decision} />

          <OutcomeSection
            key={requestKey}
            decision={decision}
          />
        </div>

        {/* footer */}
        <div
          style={{
            padding: 'var(--sa-space-4) var(--sa-space-6)',
            borderTop: '1px solid var(--sa-border)',
            display: 'flex',
            flexDirection: 'column',
            gap: 'var(--sa-space-3)',
          }}
        >
          <div
            style={{
              fontSize: 'var(--sa-text-sm)',
              color: 'var(--sa-text-muted)',
            }}
          >
            <span title={(decision.orders ?? []).flatMap((order) => [
              `${order.leg}: ${formatNyTitle(order.created_at)}`,
              ...order.fills.map((fill) => `fill: ${formatNyTitle(fill.filled_at)}`),
            ]).join(' · ')}>{orderStatusText(decision, backtest)}</span>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-4)' }}>
            <Link to={backtest ? '/backtest' : '/positions'} style={{ color: 'var(--sa-accent)', minHeight: 'var(--sa-control-height)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '0 var(--sa-space-3)', border: '1px solid var(--sa-border-strong)', borderRadius: 'var(--sa-radius-md)' }}>
              {backtest ? 'Back to backtest' : 'Open position & orders'}
            </Link>
            {!backtest && decision.llm_call_id !== null && (
              <Link
                to={`/inspector?call=${decision.llm_call_id}`}
                style={{ color: 'var(--sa-accent)', minHeight: 'var(--sa-control-height)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '0 var(--sa-space-3)', border: '1px solid var(--sa-border-strong)', borderRadius: 'var(--sa-radius-md)' }}
              >
                Open model call
              </Link>
            )}
          </div>
        </div>
    </DrawerShell>
  );
}
