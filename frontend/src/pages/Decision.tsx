import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { getDecision } from '../api/state';
import { Chip } from '../components/Chip';
import type { ChipVariant } from '../components/Chip';
import { displayTime } from '../format';
import type {
  CodeCheck,
  DecisionDetail,
  DecisionOutcome,
  OrderRow,
  RulesStatus,
} from '../types/api';
import {
  actionLabel,
  formatAmsDateTime,
  formatAmsTime,
  formatConfidence,
  formatNumber,
  formatNyTitle,
} from '../components/decisionFormat';

function actionChipVariant(action: string): ChipVariant {
  switch (action) {
    case 'enter':
      return 'positive';
    case 'skip':
      return 'warning';
    case 'exit':
      return 'negative';
    case 'tighten_stop':
      return 'info';
    default:
      return 'neutral';
  }
}

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

function Section({ label, right, children }: { label: string; right?: React.ReactNode; children: React.ReactNode }) {
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

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <>
      <span style={{ color: 'var(--sa-text-muted)' }}>{label}</span>
      <span
        style={{
          fontFamily: 'var(--sa-font-mono)',
          textAlign: 'right',
          color: 'var(--sa-text-2)',
        }}
      >
        {value}
      </span>
    </>
  );
}

function FieldGrid({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'minmax(0, 1fr) auto',
        gap: '6px var(--sa-space-4)',
        fontSize: 'var(--sa-text-md)',
      }}
    >
      {children}
    </div>
  );
}

function OutcomeTiles({ outcome }: { outcome: DecisionOutcome | null }) {
  const tiles: { label: string; price: string | null }[] = [
    { label: 'After 1h', price: outcome?.price_1h ?? null },
    { label: 'After 1 session', price: outcome?.price_1session ?? null },
    { label: 'After 5 sessions', price: outcome?.price_5sessions ?? null },
  ];
  return (
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
              border: `1px ${tile.price ? 'solid var(--sa-border)' : 'dashed var(--sa-border-strong)'}`,
              borderRadius: 'var(--sa-radius-md)',
              display: 'flex',
              flexDirection: 'column',
              gap: '4px',
            }}
          >
            <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
              {tile.label}
            </span>
            <span
              style={{
                fontFamily: 'var(--sa-font-mono)',
                color: tile.price ? 'var(--sa-text)' : 'var(--sa-text-muted)',
              }}
            >
              {tile.price ?? 'pending'}
            </span>
          </div>
        ))}
      </div>
      {outcome?.best_price && outcome.worst_price && (
        <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          While open: best {outcome.best_price} · worst {outcome.worst_price}
        </div>
      )}
    </>
  );
}

function CheckList({ checks }: { checks: CodeCheck[] }) {
  return (
    <div>
      {checks.map((check, i) => (
        <div
          key={i}
          style={{
            display: 'flex',
            gap: '10px',
            alignItems: 'flex-start',
            padding: 'var(--sa-space-2) 0',
            borderBottom:
              i < checks.length - 1 ? '1px solid var(--sa-border)' : 'none',
          }}
        >
          <span
            style={{
              color: check.passed ? 'var(--sa-positive)' : 'var(--sa-negative)',
              marginTop: '1px',
            }}
            aria-hidden="true"
          >
            {check.passed ? '✓' : '✕'}
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 'var(--sa-text-md)' }}>{check.name}</div>
            {check.detail && (
              <div
                style={{
                  fontSize: 'var(--sa-text-sm)',
                  color: 'var(--sa-text-muted)',
                  marginTop: '2px',
                }}
              >
                {check.detail}
              </div>
            )}
          </div>
          <div
            style={{
              fontFamily: 'var(--sa-font-mono)',
              fontSize: 'var(--sa-text-sm)',
              color: 'var(--sa-text-2)',
              textAlign: 'right',
              whiteSpace: 'nowrap',
            }}
          >
            {check.value ?? ''}
          </div>
        </div>
      ))}
    </div>
  );
}

function RulesSection({ rules }: { rules: RulesStatus }) {
  const checks: CodeCheck[] = rules.checks.map((check) => ({
    name: check.name,
    passed: check.passed,
    detail: check.detail,
    value: null,
  }));
  const passed = checks.filter((check) => check.passed).length;
  return (
    <Section label="Rules pre-filter" right={`${passed} of ${checks.length} passed`}>
      <CheckList checks={checks} />
      {rules.reason && (
        <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          {rules.reason}
        </div>
      )}
    </Section>
  );
}

function InputSnapshot({ snapshot }: { snapshot: Record<string, unknown> }) {
  const ind1h = (snapshot.indicators_1h ?? {}) as Record<string, unknown>;
  const daily = (snapshot.indicators_daily ?? {}) as Record<string, unknown>;
  const regime = (snapshot.regime ?? {}) as Record<string, unknown>;
  const price = snapshot.price;
  const vwap = ind1h.vwap;
  const vwapNote =
    typeof price === 'number' && typeof vwap === 'number'
      ? ` · price ${price >= vwap ? 'above' : 'below'}`
      : '';
  const close = daily.close;
  const sma50 = daily.sma50;
  const sma200 = daily.sma200;
  const smaNote =
    typeof close === 'number'
      ? `${typeof sma50 === 'number' && close > sma50 ? 'above' : 'below'} / ${
          typeof sma200 === 'number' && close > sma200 ? 'above' : 'below'
        }`
      : '—';
  const headlines = snapshot.headlines;
  const headlineCount = Array.isArray(headlines) ? headlines.length : null;
  const days = snapshot.days_to_earnings;

  return (
    <FieldGrid>
      <Field label="Price (ask)" value={formatNumber(price)} />
      <Field
        label="Regime (SPY)"
        value={
          typeof regime.trend === 'string'
            ? `${regime.trend} · ${regime.volatility}`
            : '—'
        }
      />
      <Field
        label="EMA 20 / EMA 50 (1h)"
        value={`${formatNumber(ind1h.ema20)} / ${formatNumber(ind1h.ema50)}`}
      />
      <Field label="RSI 14 (1h)" value={formatNumber(ind1h.rsi14, 1)} />
      <Field label="ATR 14 (1h)" value={formatNumber(ind1h.atr14)} />
      <Field
        label="Session VWAP"
        value={`${formatNumber(vwap)}${vwapNote}`}
      />
      <Field label="OBV trend (20 bars)" value={String(ind1h.obv_trend ?? '—')} />
      <Field
        label="RS vs SPY (20 bars)"
        value={
          typeof ind1h.rs_vs_spy_pct === 'number'
            ? `${ind1h.rs_vs_spy_pct >= 0 ? '+' : ''}${ind1h.rs_vs_spy_pct}%`
            : '—'
        }
      />
      <Field label="Daily vs SMA 50 / 200" value={smaNote} />
      <Field
        label="Opening gap"
        value={
          typeof daily.gap_pct === 'number'
            ? `${daily.gap_pct >= 0 ? '+' : ''}${daily.gap_pct}%`
            : '—'
        }
      />
      <Field
        label="Next earnings"
        value={typeof days === 'number' ? `${days} sessions` : String(days ?? '—')}
      />
      <Field
        label="Headlines (24h)"
        value={headlineCount === null ? String(headlines ?? '—') : headlineCount}
      />
    </FieldGrid>
  );
}

function ModelOutputSection({ decision }: { decision: DecisionDetail }) {
  const output = decision.model_output;
  const validation = decision.validation_result;
  if (output === null) {
    return (
      <Section
        label="Model output"
        right={<Link to="/inspector" style={{ color: 'var(--sa-accent)' }}>View model call</Link>}
      >
        <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          {validation?.error
            ? `No usable output — ${validation.error}`
            : 'No model output stored for this decision.'}
        </div>
      </Section>
    );
  }
  return (
    <Section
      label="Model output"
      right={<Link to="/inspector" style={{ color: 'var(--sa-accent)' }}>View model call</Link>}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sa-space-3)' }}>
        <div
          style={{
            minWidth: '88px',
            height: '48px',
            padding: '0 14px',
            borderRadius: 'var(--sa-radius-md)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontFamily: 'var(--sa-font-mono)',
            fontWeight: 600,
            fontSize: 'var(--sa-text-lg)',
            background:
              output.action === 'enter'
                ? 'var(--sa-positive-bg)'
                : output.action === 'exit'
                  ? 'var(--sa-negative-bg)'
                  : 'var(--sa-raised)',
            color:
              output.action === 'enter'
                ? 'var(--sa-positive)'
                : output.action === 'exit'
                  ? 'var(--sa-negative-text)'
                  : 'var(--sa-text-2)',
            whiteSpace: 'nowrap',
          }}
        >
          {output.action.replace('_', ' ').toUpperCase()}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
          <span style={{ fontWeight: 600 }}>{actionLabel(output.action)}</span>
          <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
            confidence{' '}
            <span style={{ fontFamily: 'var(--sa-font-mono)', color: 'var(--sa-text-2)' }}>
              {formatConfidence(output.confidence)}
            </span>
          </span>
        </div>
      </div>
      {output.thesis && (
        <p style={{ margin: '4px 0 0', lineHeight: 1.55, color: 'var(--sa-text-2)' }}>
          {output.thesis}
        </p>
      )}
      <FieldGrid>
        <Field label="Stop" value={formatNumber(output.stop_price)} />
        <Field label="Target" value={formatNumber(output.target_price)} />
        <Field label="Invalidation" value={output.invalidation ?? '—'} />
      </FieldGrid>
      {output.risks.length > 0 && (
        <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          Risks named:{' '}
          <span style={{ color: 'var(--sa-text-2)' }}>{output.risks.join('; ')}</span>
        </div>
      )}
      {output.news_used.length > 0 && (
        <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          News used:{' '}
          <span style={{ color: 'var(--sa-text-2)' }}>{output.news_used.join(' · ')}</span>
        </div>
      )}
    </Section>
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

function GatesSection({ gates }: { gates: GatesResult | null }) {
  if (!gates || !Array.isArray(gates.checks) || gates.checks.length === 0) {
    return (
      <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
        {gates?.blocked_by
          ? `Blocked: ${String(gates.blocked_by).replace(/_/g, ' ')}`
          : 'Risk gates run with order placement in the execution step.'}
      </div>
    );
  }
  const checks: CodeCheck[] = gates.checks.map((c) => ({
    name: c.name.replace(/_/g, ' '),
    passed: c.passed,
    detail: c.detail,
    value: null,
  }));
  return (
    <>
      {gates.pending_approval && (
        <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-accent)' }}>
          Passed the gates — waiting for approval (supervised mode).
        </div>
      )}
      <CheckList checks={checks} />
      {gates.sizing && (
        <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          Sized to {gates.sizing.shares} shares
          {gates.sizing.limited_by?.length
            ? ` · limited by ${gates.sizing.limited_by.join(', ')}`
            : ''}
          {gates.blocked_by && ` · blocked by ${String(gates.blocked_by).replace(/_/g, ' ')}`}
        </div>
      )}
    </>
  );
}

function shortId(id: string | null): string {
  if (!id) return '—';
  return `${id.slice(0, 4)}…${id.slice(-4)}`;
}

function OrderSection({ orders }: { orders: OrderRow[] }) {
  const entry = orders.find((o) => o.leg === 'entry');
  const legs = orders.filter((o) => o.leg !== 'entry');
  const stop = legs.find((o) => o.leg === 'stop');
  const target = legs.find((o) => o.leg === 'target');
  const fill = entry?.fills[0];
  return (
    <Section label="Order at Alpaca">
      <FieldGrid>
        <Field label="Type" value="Bracket · gtc" />
        <Field
          label="Entry"
          value={`limit ${entry?.limit_price ?? '—'} · ${entry?.status ?? '—'}`}
        />
        <Field
          label="Stop leg"
          value={stop ? `stop ${stop.stop_price ?? '—'} · ${stop.status}` : '—'}
        />
        <Field
          label="Target leg"
          value={target ? `limit ${target.limit_price ?? '—'} · ${target.status}` : '—'}
        />
        <Field label="Quantity" value={`${entry?.qty ?? '—'} shares`} />
        <Field label="Sent" value={displayTime(entry?.created_at ?? null)} />
        <Field
          label="Filled"
          value={
            fill
              ? `${displayTime(fill.filled_at)} @ ${fill.price}`
              : '—'
          }
        />
        {fill?.slippage != null && (
          <Field
            label="Slippage vs limit"
            value={`${Number(fill.slippage) <= 0 ? '−' : '+'}$${Math.abs(Number(fill.slippage)).toFixed(2)} / share (${Number(fill.slippage) <= 0 ? 'better' : 'worse'})`}
          />
        )}
      </FieldGrid>
      <div style={{ fontSize: 'var(--sa-text-sm)', fontFamily: 'var(--sa-font-mono)', color: 'var(--sa-text-muted)' }}>
        Alpaca order {shortId(entry?.alpaca_order_id ?? null)}
        {legs.length > 0 && ` · legs ${legs.map((l) => shortId(l.alpaca_order_id)).join(', ')}`}
      </div>
    </Section>
  );
}

function ApprovalSection({ decision }: { decision: DecisionDetail }) {
  const approval = decision.approval;
  if (!approval) return null;
  const variant: ChipVariant =
    approval.status === 'approved'
      ? 'positive'
      : approval.status === 'pending'
        ? 'warning'
        : 'negative';
  return (
    <Section label="Approval" right="supervised mode">
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sa-space-3)', flexWrap: 'wrap' }}>
        <Chip variant={variant}>{approval.status}</Chip>
        <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-md)' }}>
          {approval.qty} shares · limit {approval.limit_price}
        </span>
      </div>
      {approval.reason && (
        <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>
          {approval.reason}
        </div>
      )}
      <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
        created {displayTime(approval.created_at)}
        {approval.resolved_at && ` · resolved ${displayTime(approval.resolved_at)}`}
      </div>
    </Section>
  );
}

export default function Decision() {
  const { id } = useParams<{ id: string }>();
  const [decision, setDecision] = useState<DecisionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    getDecision(Number(id))
      .then(setDecision)
      .catch(() => setError('Decision not found.'));
  }, [id]);

  if (error) {
    return (
      <div style={{ maxWidth: 'var(--sa-drawer-width)', margin: '0 auto' }}>
        <div
          style={{
            background: 'var(--sa-chrome)',
            border: '1px solid var(--sa-border)',
            borderRadius: 'var(--sa-radius-lg)',
            padding: 'var(--sa-space-5) var(--sa-space-6)',
            color: 'var(--sa-text-muted)',
          }}
        >
          {error} <Link to="/inspector" style={{ color: 'var(--sa-accent)' }}>Back to the inspector</Link>
        </div>
      </div>
    );
  }
  if (!decision) return null;

  const regime = (decision.input_snapshot?.regime ?? null) as {
    trend?: string;
    volatility?: string;
  } | null;
  const validation = decision.validation_result;

  return (
    <div style={{ display: 'flex', justifyContent: 'center' }}>
      <div
        style={{
          width: '100%',
          maxWidth: 'var(--sa-drawer-width)',
          boxSizing: 'border-box',
          background: 'var(--sa-chrome)',
          border: '1px solid var(--sa-border)',
          borderRadius: 'var(--sa-radius-lg)',
          color: 'var(--sa-text)',
          padding: 'var(--sa-space-5) var(--sa-space-6)',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
            gap: 'var(--sa-space-3)',
            paddingBottom: 'var(--sa-space-4)',
          }}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <div
              style={{
                fontSize: 'var(--sa-text-sm)',
                color: 'var(--sa-text-muted)',
                textTransform: 'uppercase',
                letterSpacing: 'var(--sa-label-tracking)',
              }}
            >
              Decision · {decision.context}
            </div>
            <h1 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>
              {decision.symbol} · {decision.final_action.replace('_', ' ')}
            </h1>
            <div
              title={formatNyTitle(decision.cycle_time)}
              style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}
            >
              {formatAmsDateTime(decision.cycle_time)} cycle · prompt{' '}
              {decision.prompt_version ?? '—'} · {decision.model ?? '—'}
            </div>
          </div>
          <Link
            to="/inspector"
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
              border: '1px solid var(--sa-border)',
              color: 'var(--sa-text-muted)',
              textDecoration: 'none',
            }}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="6" y1="6" x2="18" y2="18" />
              <line x1="18" y1="6" x2="6" y2="18" />
            </svg>
          </Link>
        </div>

        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: '6px',
            paddingBottom: 'var(--sa-space-4)',
          }}
        >
          <Chip variant={actionChipVariant(decision.final_action)}>
            {decision.final_action === 'no_call' && decision.no_call_reason
              ? `no call · ${decision.no_call_reason.replace(/_/g, ' ')}`
              : decision.final_action.replace('_', ' ')}
          </Chip>
          {validation?.changed_by_code && (
            <Chip variant="warning">
              model said {validation.model_action?.replace('_', ' ')} — code changed it
            </Chip>
          )}
          {regime?.trend && (
            <Chip variant="neutral">
              regime {regime.trend} · {regime.volatility}
            </Chip>
          )}
        </div>

        <Section label="Outcome" right="every decision is tracked, skips too">
          <OutcomeTiles outcome={decision.outcome} />
        </Section>

        <ModelOutputSection decision={decision} />

        <Section label="Code checks">
          {validation && validation.checks.length > 0 ? (
            <CheckList checks={validation.checks} />
          ) : (
            <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
              {validation?.note ?? 'No checks recorded.'}
            </div>
          )}
        </Section>

        <Section label="Risk gates" right="in order; first failure blocks">
          <GatesSection gates={decision.gates_result as GatesResult | null} />
        </Section>

        {decision.approval && <ApprovalSection decision={decision} />}

        {decision.orders && decision.orders.length > 0 && (
          <OrderSection orders={decision.orders} />
        )}

        {decision.rules_result && <RulesSection rules={decision.rules_result} />}

        <Section label="Input snapshot" right={`bars to ${formatAmsTime(decision.cycle_time)}`}>
          {decision.input_snapshot ? (
            <InputSnapshot snapshot={decision.input_snapshot} />
          ) : (
            <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
              No snapshot stored.
            </div>
          )}
        </Section>
      </div>
    </div>
  );
}
