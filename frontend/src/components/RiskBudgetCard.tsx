import type { ReactNode } from 'react';

import type { Resource } from '../api/useResource';
import type { BudgetMeter, BudgetMeterLevel, RiskBudgetResponse } from '../types/api';
import { CardBody } from './cardState';
import {
  budgetHints,
  dayTradesText,
  drawdownUsed,
  pct2,
  riskPerTradeText,
  roomText,
  sectorsText,
  shockBrakeText,
  trimDecimal,
} from './riskBudgetFormat';

const hintStyle: React.CSSProperties = {
  textDecoration: 'underline dotted',
  textUnderlineOffset: '3px',
  cursor: 'help',
};

function meterColor(level: BudgetMeterLevel | undefined): string {
  if (level === 'red') return 'var(--sa-negative)';
  if (level === 'amber') return 'var(--sa-accent)';
  return 'var(--sa-neutral-chip)';
}

function MeterRow({
  label,
  hint,
  value,
  meter,
}: {
  label: string;
  hint: string;
  value: ReactNode;
  meter: BudgetMeter | null;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          gap: 'var(--sa-space-3)',
          fontSize: 'var(--sa-text-md)',
        }}
      >
        <span title={hint} style={{ ...hintStyle, color: 'var(--sa-text-2)' }}>
          {label}
        </span>
        <span style={{ fontFamily: 'var(--sa-font-mono)', color: 'var(--sa-text-2)' }}>
          {value}
        </span>
      </div>
      <div
        style={{
          height: '8px',
          background: 'var(--sa-border)',
          borderRadius: '4px',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            width: `${meter?.fill_pct ?? '0'}%`,
            height: '8px',
            borderRadius: '4px',
            background: meterColor(meter?.level),
          }}
        />
      </div>
    </div>
  );
}

function FactRow({
  label,
  hint,
  value,
  warn,
}: {
  label: string;
  hint: string;
  value: string;
  warn?: boolean;
}) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        gap: 'var(--sa-space-3)',
        padding: '7px 0',
        borderBottom: '1px solid var(--sa-subtle)',
        fontSize: 'var(--sa-text-md)',
      }}
    >
      <span title={hint} style={{ ...hintStyle, color: 'var(--sa-text-muted)' }}>
        {label}
      </span>
      <span
        style={{
          color: warn ? 'var(--sa-warning-text)' : 'var(--sa-text-2)',
          textAlign: 'right',
        }}
      >
        {value}
      </span>
    </div>
  );
}

function Limit({ children }: { children: ReactNode }) {
  return <span style={{ color: 'var(--sa-text-muted)' }}>{children}</span>;
}

export function RiskBudgetCard({ res }: { res: Resource<RiskBudgetResponse> }) {
  const budget = res.data;
  const sb = budget?.shock_brake;
  const sectorCap = budget?.sectors[0]?.cap ?? 0;
  return (
    <section
      aria-label="Risk budget"
      aria-busy={res.loading || undefined}
      style={{
        flex: '1 1 560px',
        minWidth: 0,
        background: 'var(--sa-panel)',
        border: '1px solid var(--sa-border)',
        borderRadius: 'var(--sa-radius-lg)',
        padding: 'var(--sa-space-4) var(--sa-space-5)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sa-space-3)',
        boxSizing: 'border-box',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          gap: 'var(--sa-space-3)',
          flexWrap: 'wrap',
        }}
      >
        <h2
          style={{
            margin: 0,
            fontSize: 'var(--sa-text-sm)',
            fontWeight: 600,
            color: 'var(--sa-text-2)',
            textTransform: 'uppercase',
            letterSpacing: 'var(--sa-label-tracking)',
          }}
        >
          Risk budget
        </h2>
        <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          {budget?.settings_version != null
            ? `limits from settings v${budget.settings_version}`
            : 'limits from settings'}
        </span>
      </div>
      <CardBody res={res} what="the risk budget">
        {() => (
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-4) 28px' }}>
        <div
          style={{
            flex: '1 1 300px',
            display: 'flex',
            flexDirection: 'column',
            gap: 'var(--sa-space-3)',
            minWidth: 0,
          }}
        >
          <MeterRow
            label="Open risk"
            hint={budgetHints.openRisk}
            meter={budget?.open_risk ?? null}
            value={
              <>
                {pct2(budget?.open_risk.used_pct)}{' '}
                <Limit>of {trimDecimal(budget?.open_risk.limit_pct)}% cap</Limit>
              </>
            }
          />
          <MeterRow
            label="Today's loss"
            hint={budgetHints.dailyLoss(budget?.daily_loss.limit_pct ?? '')}
            meter={budget?.daily_loss ?? null}
            value={
              <>
                {pct2(budget?.daily_loss.used_pct)}{' '}
                <Limit>of −{trimDecimal(budget?.daily_loss.limit_pct)}% limit</Limit>
              </>
            }
          />
          <MeterRow
            label="Drawdown"
            hint={budgetHints.drawdown(budget?.drawdown.limit_pct ?? '')}
            meter={budget?.drawdown ?? null}
            value={
              <>
                {budget ? drawdownUsed(budget.drawdown) : '—'}{' '}
                <Limit>of −{trimDecimal(budget?.drawdown.limit_pct)}% kill</Limit>
              </>
            }
          />
        </div>
        <div style={{ flex: '1 1 260px', minWidth: 0 }}>
          <FactRow
            label="Room for"
            hint={budgetHints.room}
            value={budget ? roomText(budget.room) : '—'}
          />
          <FactRow
            label="Sectors"
            hint={budgetHints.sectors(sectorCap)}
            value={budget ? sectorsText(budget.sectors) : '—'}
          />
          <FactRow
            label="Risk per trade"
            hint={budgetHints.riskPerTrade(
              budget?.losing_streak ?? { max: 0, factor: '1' },
            )}
            value={budget ? riskPerTradeText(budget) : '—'}
          />
          <FactRow
            label="Shock brake"
            hint={budgetHints.shockBrake(
              sb?.limit_1h_pct ?? '',
              sb?.limit_session_pct ?? '',
            )}
            value={budget ? shockBrakeText(budget) : '—'}
            warn={sb?.on}
          />
          <FactRow
            label="Day trades"
            hint={budgetHints.dayTrades(budget?.day_trades.max ?? 0)}
            value={budget ? dayTradesText(budget) : '—'}
          />
        </div>
      </div>
        )}
      </CardBody>
    </section>
  );
}
