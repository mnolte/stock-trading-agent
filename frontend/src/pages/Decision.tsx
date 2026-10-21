import { Fragment, useCallback, useEffect, useState } from 'react';
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import type { Location } from 'react-router-dom';

import { getBacktestDecision, getDecision, postVerdict } from '../api/state';
import { Chip } from '../components/Chip';
import { displayDayTime, nyTime } from '../format';
import type { DecisionDetail, VerdictValue } from '../types/api';
import { VerdictButtons } from '../components/VerdictButtons';
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
import { actionChip } from './dashboardFeed';
import {
  firstTouchLabel,
  gateRows,
  num,
  orderStatusText,
  signalRows,
  signedPct,
  sipBarEnd,
} from './decisionDetail';

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

function gateStateColor(state: 'pass' | 'fail' | 'reduced'): string {
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
    .map((id) => {
      const found = headlines.find((h) => h.id === id);
      return found
        ? `“${found.title}” · ${found.source} · ${nyTime(found.time)}`
        : id;
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
            <Chip key={risk} variant="warning">
              Risk: {risk}
            </Chip>
          ))}
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
        {value ?? '—'}
      </span>
    </div>
  );
}

function VerdictRow({
  decision,
  onResolved,
}: {
  decision: DecisionDetail;
  onResolved: () => void;
}) {
  const verdict = decision.verdict;
  if (!decision.verdict_eligible) return null;
  const pick = (value: VerdictValue) => {
    if (!verdict) return;
    postVerdict(verdict.id, value).then(onResolved).catch(console.error);
  };
  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: 'var(--sa-space-2)',
        marginTop: 'var(--sa-space-3)',
      }}
    >
      {verdict ? (
        <>
          <span
            title={
              verdict.source === 'draft'
                ? 'A draft verdict — confirm or change it. Only confirmed verdicts count in reviews.'
                : 'Confirmed by you; counts in reviews.'
            }
            style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}
          >
            {verdict.source === 'draft' ? 'Draft verdict:' : 'Verdict:'}
          </span>
          <Chip variant={verdictVariant(verdict.verdict)}>
            {verdictLabel(verdict.verdict)}
          </Chip>
          {verdict.reason && (
            <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
              {verdict.reason}
            </span>
          )}
          <VerdictButtons current={verdict.verdict} onPick={pick} />
        </>
      ) : (
        <span
          title="A draft verdict is written once the 5-session result is in; you confirm or change it."
        >
          <Chip variant="neutral">
            Verdict: draft after {dueDayLabel(decision.verdict_due)}
          </Chip>
        </span>
      )}
      <Link to="/reviews" style={{ fontSize: 'var(--sa-text-sm)' }}>
        Verdicts in Reviews
      </Link>
    </div>
  );
}

function OutcomeSection({
  decision,
  onVerdictResolved,
}: {
  decision: DecisionDetail;
  onVerdictResolved: () => void;
}) {
  const outcome = decision.outcome;
  const hasTrade = decision.trade !== null && decision.trade.status !== 'none';
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
  if (outcome?.best_r) soFar.push(`best ${outcome.best_r}R`);
  else if (outcome?.best_price) soFar.push(`best ${outcome.best_price}`);
  if (outcome?.worst_r) soFar.push(`worst ${outcome.worst_r}R`);
  else if (outcome?.worst_price) soFar.push(`worst ${outcome.worst_price}`);
  if (touchLabel) soFar.push(`first touch: ${touchLabel}`);

  return (
    <Section
      label="Outcome"
      right={
        decision.next_outcome_due
          ? `next result ${displayDayTime(decision.next_outcome_due)}`
          : 'all results in'
      }
    >
      {outcome === null ? (
        muted('No outcome row stored for this decision.')
      ) : (
        <>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
              gap: 'var(--sa-space-2)',
            }}
          >
            {tiles.map((tile) => (
              <div
                key={tile.label}
                style={{
                  padding: '10px',
                  border: `1px ${
                    tile.price
                      ? 'solid var(--sa-border)'
                      : 'dashed var(--sa-border-strong)'
                  }`,
                  borderRadius: 'var(--sa-radius-md)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '4px',
                }}
              >
                <span
                  style={{
                    fontSize: 'var(--sa-text-sm)',
                    color: 'var(--sa-text-muted)',
                  }}
                >
                  {tile.label}
                </span>
                <span
                  style={{
                    fontFamily: 'var(--sa-font-mono)',
                    fontSize: 'var(--sa-text-md)',
                    color: tile.price ? 'var(--sa-text)' : 'var(--sa-text-muted)',
                  }}
                >
                  {tile.price
                    ? `${tile.price}${tile.r ? ` · ${tile.r}R` : ''}`
                    : 'pending'}
                </span>
                {tile.at && (
                  <span
                    title="Timestamp of the 5-minute bar the price was taken from"
                    style={{
                      fontSize: 'var(--sa-text-xs)',
                      color: 'var(--sa-text-muted)',
                    }}
                  >
                    bar {nyTime(tile.at)} NY
                  </span>
                )}
              </div>
            ))}
          </div>
          {soFar.length > 0 && (
            <div
              style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}
            >
              So far: {soFar.join(' · ')}
            </div>
          )}
          {hasTrade && !touchLabel && (
            <div
              style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}
            >
              first touch: —
            </div>
          )}
        </>
      )}
      <VerdictRow decision={decision} onResolved={onVerdictResolved} />
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
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 100 }}>
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
        role="dialog"
        aria-modal="true"
        aria-label={label}
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
    </div>
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
  const fetchDecision = useCallback(
    (decisionId: number) =>
      btRun && btSlug
        ? getBacktestDecision(Number(btRun), btSlug, decisionId)
        : getDecision(decisionId),
    [btRun, btSlug],
  );
  const location = useLocation();
  const navigate = useNavigate();
  const [decision, setDecision] = useState<DecisionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  useEffect(() => {
    if (!id) return;
    fetchDecision(Number(id))
      .then(setDecision)
      .catch(() => setError('Decision not found.'));
  }, [id, fetchDecision]);

  if (error) {
    return (
      <DrawerShell label="Decision detail" onClose={close}>
        <div
          style={{
            padding: 'var(--sa-space-5) var(--sa-space-6)',
            color: 'var(--sa-text-muted)',
          }}
        >
          {error}{' '}
          <Link to="/" style={{ color: 'var(--sa-accent)' }}>
            Back to the dashboard
          </Link>
        </div>
      </DrawerShell>
    );
  }
  if (!decision) {
    return (
      <DrawerShell label="Decision detail" onClose={close}>
        {muted('Loading…')}
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
  const chip = actionChip(decision.final_action, decision.no_call_reason);
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
                  {trade.qty} sh @ {trade.entry}
                  {hasOrder ? ' limit' : ''}
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
                  confidence
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

          <SafetyGates decision={decision} />
          <SignalStack decision={decision} />
          <Reasoning decision={decision} />

          {/* level tiles */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
              gap: 'var(--sa-space-2)',
              padding: 'var(--sa-space-4) 0 0',
              borderTop: '1px solid var(--sa-border)',
            }}
          >
            <LevelTile label="Entry" value={trade?.entry ?? null} />
            <LevelTile
              label="Stop"
              value={trade?.stop ?? null}
              accent="var(--sa-negative-text)"
            />
            <LevelTile
              label="Target"
              value={trade?.target ?? null}
              accent="var(--sa-positive)"
            />
            <LevelTile
              label="Invalidation · 1h close"
              value={output?.invalidation ?? null}
            />
          </div>

          <OutcomeSection
            decision={decision}
            onVerdictResolved={() => {
              if (id)
                fetchDecision(Number(id))
                  .then(setDecision)
                  .catch(console.error);
            }}
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
            {orderStatusText(decision)}
          </div>
          <div style={{ display: 'flex', gap: 'var(--sa-space-4)' }}>
            <Link to="/positions" style={{ color: 'var(--sa-accent)' }}>
              Open position &amp; orders
            </Link>
            <Link
              to={`/inspector?call=${decision.id}`}
              style={{ color: 'var(--sa-accent)' }}
            >
              Open model call
            </Link>
          </div>
        </div>
    </DrawerShell>
  );
}
