import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useSearchParams } from 'react-router-dom';

import { useLiveUpdates } from '../api/liveContext';
import {
  getApprovals,
  getAttention,
  getChart,
  getDashboard,
  getPositions,
  getRiskBudget,
  getWatchlist,
} from '../api/state';
import { useApiResource } from '../api/useResource';
import type { FetchFailure, Resource } from '../api/useResource';
import type {
  DashboardFeedItem,
  DashboardLatestDecision,
  DashboardResponse,
  MarketClosedCard,
  PositionRow,
  PositionsResponse,
} from '../types/api';
import { ApprovalCard } from '../components/ApprovalCard';
import { NeedsYouCard } from '../components/NeedsYouCard';
import { StopTargetBar } from '../components/StopTargetBar';
import { Banner } from '../components/Banner';
import { BriefingCard } from '../components/BriefingCard';
import { Card } from '../components/Card';
import { CardBoundary } from '../components/CardBoundary';
import { CardBody, ErrorBanner } from '../components/cardState';
import { Chip } from '../components/Chip';
import { CycleCounter } from '../components/CycleCounter';
import { DialogButton, Modal } from '../components/Modal';
import { PriceChart } from '../components/PriceChart';
import { RiskBudgetCard } from '../components/RiskBudgetCard';
import { StatCard } from '../components/StatCard';
import { Watchlist } from '../components/Watchlist';
import {
  SkeletonChart,
  SkeletonDecision,
  SkeletonList,
  SkeletonText,
  SkeletonTile,
} from '../components/skeletons';
import { formatDayChange, formatPrice } from '../components/watchlistFormat';
import { vixCardTitle, vixChipLabel } from '../vix';
import {
  displayFullDate,
  displayTime,
  displayWeekdayDateTime,
  displayWeekdayTime,
  elapsedHm,
  fmtDayMonthIn,
  fmtWeekdayDayShort,
  formatDuration,
  joinParts,
  nyTime,
  sessionDayLabel,
  sessionDayShort,
  signed,
  signedPct,
  signedUsd,
} from '../format';
import { CycleValues } from '../components/cycleLine';
import { cycleSentenceParts } from '../components/cycleLineFormat';
import {
  actionBadgeStyle,
  formatAmsTime,
  formatNyTitle,
  noCallLine,
} from '../components/decisionFormat';
import { earningsChipLabel } from './decisionDetail';
import {
  actionText,
  collapsedMeta,
  cycleMeta,
  groupFeed,
  rowSymbols,
  type FeedCycle,
  type FeedRow,
} from './dashboardFeed';

/** One feed row: a single decision links to the decision drawer, a grouped
 * row to the LLM inspector filtered to its cycle and action. */
function FeedRowLink({ row }: { row: FeedRow }) {
  const location = useLocation();
  const { text, more } = rowSymbols(row.items);
  const single = row.items.length === 1;
  const item = row.items[0];
  const to = single
    ? `/decisions/${item.id}`
    : `/inspector?cycle=${encodeURIComponent(item.time)}&action=${row.action}`;
  return (
    <Link
      to={to}
      state={single ? { backgroundLocation: location } : undefined}
      className="sa-row sa-feed-row"
      style={{
        display: 'grid',
        gridTemplateColumns: '108px minmax(0, 1fr) auto',
        gap: '10px',
        alignItems: 'center',
        minHeight: 'var(--sa-control-height)',
        padding: '6px 8px',
        margin: '0 -8px',
        borderRadius: 'var(--sa-radius-sm)',
        borderBottom: '1px solid var(--sa-border)',
        fontSize: 'var(--sa-text-md)',
        color: 'var(--sa-text-2)',
        textDecoration: 'none',
      }}
    >
      <span
        style={{
          display: 'inline-block',
          minWidth: '56px',
          fontFamily: 'var(--sa-font-mono)',
          fontSize: 'var(--sa-text-sm)',
          fontWeight: 600,
          color: row.color,
          whiteSpace: 'nowrap',
        }}
      >
        {row.label}
      </span>
      <span style={{ minWidth: 0 }}>
        <b style={{ fontWeight: 600, color: 'var(--sa-text)' }}>{text}</b>
        {more > 0 && (
          <span style={{ color: 'var(--sa-text-muted)' }}> +{more} more</span>
        )}
        {row.reason && (
          <span style={{ color: 'var(--sa-text-muted)' }}> · {row.reason}</span>
        )}
      </span>
      <span
        style={{
          fontFamily: 'var(--sa-font-mono)',
          fontSize: 'var(--sa-text-sm)',
          color: 'var(--sa-text-muted)',
        }}
      >
        {row.items.length > 1 ? row.items.length : ''}
      </span>
    </Link>
  );
}

function CycleGroup({
  cycle,
  expanded,
  onToggle,
}: {
  cycle: FeedCycle;
  expanded: boolean;
  onToggle: () => void;
}) {
  const rows = expanded ? cycle.rows : cycle.changeRows;
  return (
    <div>
      <button
        type="button"
        aria-expanded={expanded}
        onClick={onToggle}
        style={{
          display: 'flex',
          width: '100%',
          alignItems: 'baseline',
          gap: '10px',
          minHeight: '40px',
          padding: 'var(--sa-space-2) 0 var(--sa-space-1)',
          border: 0,
          background: 'transparent',
          color: 'var(--sa-text)',
          textAlign: 'left',
          cursor: 'pointer',
        }}
      >
        <span aria-hidden="true" style={{ color: 'var(--sa-text-muted)' }}>
          {expanded ? '▾' : '▸'}
        </span>
        <span
          title={formatNyTitle(cycle.time)}
          style={{
            fontFamily: 'var(--sa-font-mono)',
            fontSize: 'var(--sa-text-md)',
            fontWeight: 600,
          }}
        >
          {formatAmsTime(cycle.time)}
        </span>
        <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          {expanded ? cycleMeta(cycle) : collapsedMeta(cycle)}
        </span>
      </button>
      {rows.map((row) => (
        <FeedRowLink key={row.key} row={row} />
      ))}
    </div>
  );
}

/** "Today's decisions" — feed grouped per cycle; the latest cycle opens,
 * earlier cycles collapse to their change rows. */
function Feed({ res }: { res: Resource<DashboardResponse> }) {
  const items = res.data?.feed ?? EMPTY_FEED;
  const cycles = useMemo(() => groupFeed(items), [items]);
  // The latest cycle starts open; every other starts collapsed to changes.
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const isOpen = (cycle: FeedCycle, first: boolean) =>
    toggled[cycle.key] ?? first;
  const sess = res.data?.session;
  // Off-hours the feed is titled with the shown session's date
  // ("Friday 23 Oct · last session", docs/SPEC.md).
  const title =
    sess && !sess.shown_is_today
      ? `${sessionDayLabel(sess.shown_date)} · last session`
      : 'Today’s decisions';

  return (
    <Card
      label={title}
      ariaLabel={title}
      right={<Link to="/inspector">All model calls</Link>}
      busy={res.loading}
      refreshing={res.refreshing}
      style={{ flex: 1, gap: '14px' }}
    >
      <CardBody
        res={res}
        what="the decision feed"
        skeleton={<SkeletonList rows={6} />}
        isEmpty={(d) => d.feed.length === 0}
        empty={
          <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
            {sess && !sess.shown_is_today
              ? `No decisions on ${sessionDayLabel(sess.shown_date)}.`
              : 'No decisions yet today — the next cycle writes the first.'}
          </div>
        }
      >
        {() => (
          <>
            {cycles.map((cycle, i) => (
              <CycleGroup
                key={cycle.key}
                cycle={cycle}
                expanded={isOpen(cycle, i === 0)}
                onToggle={() =>
                  setToggled((t) => ({ ...t, [cycle.key]: !isOpen(cycle, i === 0) }))
                }
              />
            ))}
            <div
              style={{
                fontSize: 'var(--sa-text-sm)',
                color: 'var(--sa-text-muted)',
                paddingTop: 'var(--sa-space-2)',
              }}
            >
              Changes (entries, exits, stop moves) always show. Other decisions are
              grouped by action and reason; collapsed cycles show their changes
              only.
            </div>
          </>
        )}
      </CardBody>
    </Card>
  );
}

const EMPTY_FEED: DashboardFeedItem[] = [];

function toneStyle(tone: number): React.CSSProperties {
  return {
    padding: '4px 8px',
    borderRadius: 'var(--sa-radius-sm)',
    fontFamily: 'var(--sa-font-mono)',
    fontSize: 'var(--sa-text-sm)',
    whiteSpace: 'nowrap',
    background:
      tone > 0
        ? 'var(--sa-positive-bg)'
        : tone < 0
          ? 'var(--sa-negative-bg)'
          : 'var(--sa-subtle)',
    color:
      tone > 0
        ? 'var(--sa-positive)'
        : tone < 0
          ? 'var(--sa-negative-text)'
          : 'var(--sa-text-2)',
  };
}

const GROUP_LABEL: React.CSSProperties = {
  fontSize: 'var(--sa-text-xs)',
  fontWeight: 600,
  color: 'var(--sa-text-muted)',
  textTransform: 'uppercase',
  letterSpacing: '0.08em',
};

/** "MSFT · 18:46 cycle · 12:46 NY · gpt-4.1-mini · decision.v6" — a
 * no_call says "no model call" instead of the model (item 073). */
function decisionMeta(d: DashboardLatestDecision): string {
  const noCall = d.final_action === 'no_call';
  const called = !noCall && d.model !== null && d.model !== 'none';
  return joinParts(
    d.symbol,
    `${displayTime(d.time)} cycle`,
    `${nyTime(d.time)} NY`,
    called ? joinParts(d.model, d.prompt_version ?? undefined) : 'no model call',
  );
}

function LatestDecision({ res }: { res: Resource<DashboardResponse> }) {
  const location = useLocation();
  const d = res.data?.latest_decision ?? null;
  // Off-session fallback (design/screens/States.dc.html): the card shows
  // the most recent decision for the symbol, with its date.
  const compact = d !== null && !d.in_session;
  const title = compact && d !== null ? `Latest decision · ${d.symbol}` : 'Latest decision';
  return (
    <Card
      ariaLabel="Latest decision"
      busy={res.loading}
      refreshing={res.refreshing}
      style={{ padding: '20px', gap: '16px' }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'baseline',
            justifyContent: 'space-between',
            gap: '8px',
            flexWrap: 'wrap',
          }}
        >
          <h2
            style={{
              margin: 0,
              fontSize: 'var(--sa-text-md)',
              fontWeight: 600,
              color: 'var(--sa-text-2)',
              textTransform: 'uppercase',
              letterSpacing: 'var(--sa-label-tracking)',
            }}
          >
            {title}
          </h2>
          {d ? (
            <Link
              to={`/decisions/${d.id}`}
              state={{ backgroundLocation: location }}
              style={{ fontSize: 'var(--sa-text-md)', whiteSpace: 'nowrap' }}
            >
              Open full decision →
            </Link>
          ) : (
            // Keeps the header's right edge stable while loading.
            <span style={{ visibility: 'hidden', fontSize: 'var(--sa-text-md)' }}>
              Open full decision →
            </span>
          )}
        </div>
        {d !== null && !compact && (
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
            {decisionMeta(d)}
          </span>
        )}
      </div>
      <CardBody
        res={res}
        what="the latest decision"
        skeleton={<SkeletonDecision />}
        isEmpty={(data) => data.latest_decision === null}
        empty={
          <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
            No decisions recorded yet — the next cycle writes the first.
          </div>
        }
      >
        {() =>
          d === null ? null : compact ? (
            <LatestDecisionCompact d={d} />
          ) : (
            <LatestDecisionBody d={d} />
          )
        }
      </CardBody>
    </Card>
  );
}

/** Compact off-session card: "HOLD · Fri 23 Oct 21:46 · trend intact". */
function LatestDecisionCompact({ d }: { d: DashboardLatestDecision }) {
  const chip = actionText(d.final_action);
  const badge = actionBadgeStyle(d.final_action);
  return (
    <div
      style={{
        fontSize: 'var(--sa-text-md)',
        color: 'var(--sa-text-2)',
        lineHeight: 1.55,
      }}
    >
      <b
        style={{
          fontFamily: 'var(--sa-font-mono)',
          fontWeight: 600,
          color: badge.color,
        }}
      >
        {chip.label}
      </b>
      {' · '}
      <span title={formatNyTitle(d.time)}>
        {displayWeekdayDateTime(d.time)}
      </span>
      {d.why ? ` · ${d.why}` : ''}
    </div>
  );
}

function LatestDecisionBody({ d }: { d: DashboardLatestDecision }) {
  const location = useLocation();
  const chip = actionText(d.final_action);
  const badge = actionBadgeStyle(d.final_action);
  const noCall = d.final_action === 'no_call';
  const regimeVariant: 'positive' | 'negative' | 'neutral' =
    d.regime?.trend === 'up'
      ? 'positive'
      : d.regime?.trend === 'down'
        ? 'negative'
        : 'neutral';
  const sub = noCall
    ? noCallLine(d)
    : joinParts(
        `confidence ${d.confidence !== null && d.confidence !== undefined ? d.confidence.toFixed(2) : '—'}`,
        d.chosen_option ? `option ${d.chosen_option}` : null,
      );
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
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
            whiteSpace: 'nowrap',
            background: badge.bg,
            color: badge.color,
          }}
        >
          {chip.label}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', minWidth: 0 }}>
          <span style={{ fontWeight: 600, fontSize: 'var(--sa-text-lg)' }}>{d.symbol}</span>
          <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.4 }}>
            {sub}
          </span>
        </div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        <span style={GROUP_LABEL}>Why</span>
        <p
          style={{
            margin: 0,
            color: 'var(--sa-text-2)',
            lineHeight: 1.55,
            display: '-webkit-box',
            WebkitLineClamp: 4,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
        >
          {d.why ?? '—'}
        </p>
        <Link
          to={`/decisions/${d.id}`}
          state={{ backgroundLocation: location }}
          style={{ alignSelf: 'flex-start', fontSize: 'var(--sa-text-md)' }}
        >
          more
        </Link>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        <span style={GROUP_LABEL}>Context</span>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
          {d.regime && (
            <span title="SPY regime: trend × volatility">
              <Chip variant={regimeVariant}>
                market {d.regime.trend} · {d.regime.volatility} vol
              </Chip>
            </span>
          )}
          {d.vix && vixChipLabel(d.vix) !== null && (
            <span title={vixCardTitle(d.vix)}>
              <Chip variant={d.vix.stale ? 'warning' : 'neutral'}>{vixChipLabel(d.vix)}</Chip>
            </span>
          )}
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              height: '24px',
              padding: '0 8px',
              borderRadius: 'var(--sa-radius-sm)',
              fontFamily: 'var(--sa-font-mono)',
              fontSize: 'var(--sa-text-sm)',
              whiteSpace: 'nowrap',
              background: 'var(--sa-subtle)',
              color: 'var(--sa-text-2)',
              border: '1px solid var(--sa-border-strong)',
            }}
          >
            {earningsChipLabel(d.earnings)}
          </span>
          {(d.release_next_morning ?? []).map((rel, i) => (
            <Chip key={`${rel.type}-${rel.date}-${i}`} variant="warning">
              {rel.label ?? rel.type.toUpperCase()} {fmtWeekdayDayShort(rel.date)}
            </Chip>
          ))}
        </div>
      </div>
      {d.sig.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <span style={GROUP_LABEL}>Signals</span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            {d.sig.map((s, i) => (
              <span key={i} style={toneStyle(s.tone)}>
                {s.text}
              </span>
            ))}
          </div>
        </div>
      )}
      {d.box.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <span style={GROUP_LABEL}>Key facts</span>
          <div
            style={{
              border: '1px solid var(--sa-border)',
              borderRadius: 'var(--sa-radius-md)',
              padding: '2px var(--sa-space-3)',
            }}
          >
            {d.box.slice(0, 3).map((row, i, rows) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  gap: 'var(--sa-space-3)',
                  padding: '8px 0',
                  fontSize: 'var(--sa-text-md)',
                  borderBottom:
                    i < rows.length - 1 ? '1px solid var(--sa-subtle)' : 'none',
                }}
              >
                <span style={{ color: 'var(--sa-text-2)' }}>{row.label}</span>
                <span
                  style={{
                    fontFamily: 'var(--sa-font-mono)',
                    fontSize: 'var(--sa-text-sm)',
                    textAlign: 'right',
                    color:
                      row.tone > 0
                        ? 'var(--sa-positive)'
                        : row.tone < 0
                          ? 'var(--sa-negative-text)'
                          : 'var(--sa-text-2)',
                  }}
                >
                  {row.value}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** " Both have a stop and target at Alpaca; MSFT earnings exit on 27 Oct."
 * — the design's second footnote sentence (Main.dc.html), emitted only
 * for what is true of the open positions. */
function brokerFootnote(open: PositionRow[]): string {
  if (open.length === 0) return '';
  const guarded = open.filter(
    (p) =>
      p.current_stop_price !== null &&
      p.target_price !== null &&
      !p.stop_missing,
  );
  const exits = open
    .map((p) => {
      const m = p.scheduled_exit?.match(/^earnings exit (\d{4}-\d{2}-\d{2})$/);
      if (!m) return null;
      const day = fmtDayMonthIn(m[1]);
      return day ? `${p.symbol} earnings exit on ${day}` : null;
    })
    .filter((s): s is string => s !== null);
  const clauses: string[] = [];
  if (guarded.length === open.length) {
    clauses.push(
      open.length === 1
        ? 'It has a stop and target at Alpaca'
        : open.length === 2
          ? 'Both have a stop and target at Alpaca'
          : 'All have a stop and target at Alpaca',
    );
  }
  clauses.push(...exits);
  return clauses.length ? ` ${clauses.join('; ')}.` : '';
}

function OpenPositionsMini({
  res,
  nextCycle,
  nextCycleToday,
  passCount,
}: {
  res: Resource<PositionsResponse>;
  /** Next cycle's ISO time, or null when unknown. */
  nextCycle: string | null;
  nextCycleToday: boolean;
  passCount: number | null;
}) {
  const nextCycleLabel = nextCycle
    ? nextCycleToday
      ? displayTime(nextCycle)
      : displayWeekdayTime(nextCycle)
    : null;
  const open = res.data?.open ?? [];
  const cap = res.data?.max_open_positions;
  const positionsLabel =
    cap !== null && cap !== undefined
      ? `Open positions · ${open.length} of ${cap}`
      : 'Open positions';
  return (
    <Card
      label={positionsLabel}
      ariaLabel={positionsLabel}
      right={<Link to="/positions">Positions & orders</Link>}
      busy={res.loading}
      refreshing={res.refreshing}
      style={{ flex: '1 1 520px', gap: '14px' }}
    >
      <CardBody
        res={res}
        what="positions"
        skeleton={<SkeletonList rows={3} />}
        isEmpty={(d) => d.open.length === 0}
        empty={
          <>
            <p style={{ margin: 0, lineHeight: 1.55, color: 'var(--sa-text-2)' }}>
              No open positions.
            </p>
            <p style={{ margin: 0, lineHeight: 1.55, color: 'var(--sa-text-muted)' }}>
              {nextCycleLabel ? (
                <>
                  The next cycle at{' '}
                  <span title={formatNyTitle(nextCycle ?? '')}>{nextCycleLabel}</span>{' '}
                  may open one
                  {passCount !== null && passCount > 0
                    ? `: ${passCount} symbol${passCount === 1 ? '' : 's'} pass${passCount === 1 ? 'es' : ''} the rules`
                    : ''}
                  .
                </>
              ) : (
                'The next entry decision comes with the next cycle.'
              )}
            </p>
          </>
        }
      >
        {() => (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          {open.map((p) => {
            const near = Number(res.data?.near_stop_atr ?? 1.5);
            const closeToStop =
              p.to_stop_atr !== null && p.to_stop_atr !== undefined && p.to_stop_atr < near;
            const last = p.last_price !== null && p.last_price !== undefined ? Number(p.last_price) : null;
            return (
              <Link
                key={p.id}
                to="/positions"
                className="sa-row"
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'minmax(0, 1fr) auto',
                  gap: '4px 16px',
                  alignItems: 'center',
                  padding: '10px 8px',
                  margin: '0 -8px',
                  borderRadius: 'var(--sa-radius-sm)',
                  borderTop: '1px solid var(--sa-border)',
                  color: 'var(--sa-text)',
                  textDecoration: 'none',
                }}
              >
                <span style={{ minWidth: 0 }}>
                  <b style={{ fontWeight: 600 }}>{p.symbol}</b>{' '}
                  {p.stop_missing && (
                    <span
                      style={{
                        fontFamily: 'var(--sa-font-mono)',
                        fontSize: 'var(--sa-text-sm)',
                        color: 'var(--sa-negative-text)',
                      }}
                    >
                      No stop at Alpaca since {displayTime(p.stop_missing.since)}{' '}
                      · {elapsedHm(p.stop_missing.since)}{' '}
                    </span>
                  )}
                  <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                    {p.qty} sh{p.opened_today ? ' · today' : ''}
                    {(p.release_dates ?? []).map((rel) => (
                      <span
                        key={`release-${rel.type}-${rel.date}`}
                        title="Held into this 08:30 release (release_next_morning)"
                      >
                        {' '}· {rel.label ?? rel.type.toUpperCase()}{' '}
                        {fmtWeekdayDayShort(rel.date)} {rel.time ?? '08:30'} NY
                      </span>
                    ))}
                  </span>{' '}
                  <span
                    className="sa-pos-dist"
                    style={{
                      fontFamily: 'var(--sa-font-mono)',
                      fontSize: 'var(--sa-text-sm)',
                      color: closeToStop ? 'var(--sa-warning-text)' : 'var(--sa-text-muted)',
                    }}
                  >
                    <span className="sa-hide-phone">· </span>
                    {p.to_stop_pct !== null && p.to_stop_pct !== undefined ? `${p.to_stop_pct.toFixed(2)}%` : '—'}{' '}
                    · {p.to_stop_atr !== null && p.to_stop_atr !== undefined ? `${p.to_stop_atr.toFixed(1)}` : '—'} ATR to stop
                  </span>
                </span>
                <span style={{ textAlign: 'right', fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-md)', whiteSpace: 'nowrap' }}>
                  <span style={{ color: (p.unrealized_pnl ?? 0) >= 0 ? 'var(--sa-positive)' : 'var(--sa-negative-text)' }}>
                    {p.unrealized_pnl !== null && p.unrealized_pnl !== undefined ? signedUsd(p.unrealized_pnl) : '—'}
                  </span>{' '}
                  <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                    {p.locked_in ? 'stop above entry' : p.r_multiple !== null && p.r_multiple !== undefined ? `${signed(p.r_multiple)}R` : ''}
                  </span>
                </span>
                <span style={{ gridColumn: '1 / -1', minWidth: 0, display: 'block' }}>
                  <StopTargetBar
                    stop={p.current_stop_price !== null ? Number(p.current_stop_price) : null}
                    entry={Number(p.avg_entry_price)}
                    last={last}
                    target={p.target_price !== null ? Number(p.target_price) : null}
                  />
                </span>
              </Link>
            );
          })}
          <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.5 }}>
            Bar: stop (red) to target (green), entry tick, white dot for the last
            price. Under {res.data?.near_stop_atr ?? '1.5'} ATR (1h) to the stop
            the distance turns amber.
            {brokerFootnote(open)}
          </div>
        </div>
        )}
      </CardBody>
    </Card>
  );
}

export function MarketClosed({
  mc,
  day,
}: {
  mc: MarketClosedCard;
  /** Session date the summary covers — "Friday 23 Oct". Defaults to "today". */
  day?: string;
}) {
  return (
    <Card>
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'flex-start',
          gap: 'var(--sa-space-3)',
          maxWidth: '760px',
        }}
      >
        <span style={{ color: 'var(--sa-text-muted)' }}>
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="9" />
            <polyline points="12 7 12 12 15 14" />
          </svg>
        </span>
        <h3 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>Market closed</h3>
        <p style={{ margin: 0, lineHeight: 1.55, color: 'var(--sa-text-2)' }}>
          {mc.next_open
            ? `The next session opens ${displayFullDate(mc.next_open)} at ${displayTime(mc.next_open)} Amsterdam (${nyTime(mc.next_open)} New York). `
            : 'The next session is not on the calendar yet. '}
          {mc.first_cycle && `The first cycle runs at ${displayTime(mc.first_cycle)}. `}
          {mc.open_count > 0
            ? `All ${mc.open_count} position${mc.open_count === 1 ? ' is' : 's are'} protected by stops at Alpaca overnight; a stop only triggers once trading opens.`
            : 'No open positions.'}
        </p>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-2)' }}>
          {mc.stops.map((s) => (
            <Chip key={s.symbol} variant="neutral">
              {s.symbol} stop {s.stop}
            </Chip>
          ))}
          {mc.today_summary && (
            <Chip variant="info">
              {day ? `${sessionDayShort(day)}` : 'today'}: {mc.today_summary}
            </Chip>
          )}
        </div>
      </div>
    </Card>
  );
}

/** "Find symbol" dialog over held symbols plus the watchlist — replaces the
 * old wall of one button per symbol (design/screens/Main.dc.html). */
function FindSymbolDialog({
  open,
  symbols,
  held,
  selected,
  onPick,
  onClose,
}: {
  open: boolean;
  symbols: string[];
  held: Set<string>;
  selected: string | null;
  onPick: (symbol: string) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setQuery('');
  }
  const options = useMemo(() => {
    const q = query.trim().toUpperCase();
    return q ? symbols.filter((s) => s.includes(q)) : symbols;
  }, [symbols, query]);
  return (
    <Modal
      open={open}
      title="Find symbol"
      onClose={onClose}
      footer={
        <DialogButton variant="cancel" onClick={onClose}>
          Close
        </DialogButton>
      }
    >
      <input
        autoFocus
        type="search"
        aria-label="Find a symbol"
        placeholder="Type to filter"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        style={{
          height: 'var(--sa-input-height)',
          boxSizing: 'border-box',
          width: '100%',
          padding: '0 var(--sa-space-3)',
          borderRadius: 'var(--sa-radius-md)',
          border: '1px solid var(--sa-border-strong)',
          background: 'var(--sa-bg)',
          color: 'var(--sa-text)',
          fontSize: 'var(--sa-text-base)',
        }}
      />
      <ul
        role="listbox"
        aria-label="Symbols"
        style={{
          margin: 0,
          padding: 0,
          listStyle: 'none',
          maxHeight: '320px',
          overflowY: 'auto',
        }}
      >
        {options.map((symbol) => (
          <li key={symbol}>
            <button
              type="button"
              role="option"
              aria-selected={symbol === selected}
              onClick={() => onPick(symbol)}
              className="sa-row"
              style={{
                width: '100%',
                display: 'flex',
                alignItems: 'center',
                gap: 'var(--sa-space-3)',
                minHeight: 'var(--sa-control-height)',
                padding: '0 var(--sa-space-3)',
                border: 0,
                borderRadius: 'var(--sa-radius-sm)',
                background: 'transparent',
                color: 'var(--sa-text)',
                fontSize: 'var(--sa-text-base)',
                fontWeight: 600,
                cursor: 'pointer',
                textAlign: 'left',
              }}
            >
              {symbol}
              {held.has(symbol) && (
                <span
                  style={{
                    fontSize: 'var(--sa-text-sm)',
                    color: 'var(--sa-info)',
                    fontWeight: 400,
                  }}
                >
                  held
                </span>
              )}
            </button>
          </li>
        ))}
        {options.length === 0 && (
          <li
            style={{
              padding: 'var(--sa-space-4)',
              color: 'var(--sa-text-muted)',
              fontSize: 'var(--sa-text-md)',
            }}
          >
            No symbol in the universe matches “{query}”.
          </li>
        )}
      </ul>
    </Modal>
  );
}

export default function Dashboard() {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();

  // The Needs you chip links to "/#needs" — React Router doesn't scroll
  // to hash anchors, so scroll here once the card is on screen.
  useEffect(() => {
    if (location.hash === '#needs') {
      document
        .getElementById('needs')
        ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [location.hash]);

  const positionsRes = useApiResource((signal) => getPositions(signal));
  const attentionRes = useApiResource((signal) => getAttention(signal));
  const watchlistRes = useApiResource((signal) => getWatchlist(signal));
  const approvalsRes = useApiResource((signal) => getApprovals(signal));
  const budgetRes = useApiResource((signal) => getRiskBudget(signal));

  // The charted symbol persists in the URL (?symbol=): an explicit param
  // wins, else the first held position, else the first watchlist row.
  const urlSymbol = useMemo(() => {
    const raw = searchParams.get('symbol');
    return raw ? raw.toUpperCase() : null;
  }, [searchParams]);
  const heldSymbols = useMemo(
    () => (positionsRes.data?.open ?? []).map((p) => p.symbol),
    [positionsRes.data],
  );
  const watchSymbols = useMemo(
    () => (watchlistRes.data?.rows ?? []).map((r) => r.symbol),
    [watchlistRes.data],
  );
  const chartSymbol = urlSymbol ?? heldSymbols[0] ?? watchSymbols[0] ?? null;
  const heldSet = useMemo(() => new Set(heldSymbols), [heldSymbols]);
  const pickerSymbols = useMemo(
    () => [...heldSymbols, ...watchSymbols.filter((s) => !heldSet.has(s))],
    [heldSymbols, watchSymbols, heldSet],
  );
  const pickSymbol = useCallback(
    (symbol: string) => setSearchParams({ symbol }),
    [setSearchParams],
  );
  useEffect(() => {
    if (chartSymbol && urlSymbol !== chartSymbol) {
      setSearchParams({ symbol: chartSymbol }, { replace: true });
    }
  }, [chartSymbol, urlSymbol, setSearchParams]);

  // The latest-decision card tracks the selected symbol.
  const dashboardRes = useApiResource(
    (signal) => getDashboard(chartSymbol ?? undefined, signal),
    [chartSymbol],
  );
  const chartRes = useApiResource(
    // reload() ignores the enabled flag, so the fetcher itself must not
    // hit /api/chart/ without a symbol.
    (signal) =>
      chartSymbol === null
        ? Promise.resolve(null)
        : getChart(chartSymbol, signal),
    [chartSymbol],
    chartSymbol !== null,
  );

  useLiveUpdates(
    ['decisions', 'header', 'performance', 'reviews'],
    dashboardRes.reload,
  );
  useLiveUpdates(['watchlist'], watchlistRes.reload);
  useLiveUpdates(['attention'], attentionRes.reload);
  useLiveUpdates(['approvals'], () => {
    approvalsRes.reload();
    dashboardRes.reload();
  });
  useLiveUpdates(['positions', 'orders'], () => {
    positionsRes.reload();
    dashboardRes.reload();
    chartRes.reload();
  });
  // The risk budget card reads the gate context: positions, orders and
  // account updates (header) all change its inputs.
  useLiveUpdates(['positions', 'orders', 'header'], budgetRes.reload);
  useLiveUpdates(['watchlist', 'decisions'], chartRes.reload);

  // Page-level state: two or more failed cards, or a request that never got
  // an answer, means the backend is probably unreachable — banner + 15 s
  // auto-retry (design/screens/States.dc.html).
  const resources = [
    dashboardRes,
    watchlistRes,
    approvalsRes,
    positionsRes,
    budgetRes,
    chartRes,
  ];
  const failures = resources
    .map((r) => r.failure)
    .filter((f): f is FetchFailure => f !== null);
  const backendDown =
    failures.some((f) => f.unreachable) || failures.length >= 2;
  const failedRef = useRef(resources.filter((r) => r.failure !== null));
  useEffect(() => {
    failedRef.current = resources.filter((r) => r.failure !== null);
  });
  useEffect(() => {
    if (!backendDown) return;
    const timer = setInterval(() => {
      failedRef.current.forEach((r) => r.reload());
    }, 15_000);
    return () => clearInterval(timer);
  }, [backendDown]);
  const retryFailed = useCallback(() => {
    failedRef.current.forEach((r) => r.reload());
  }, []);
  const failSince =
    failures.length > 0
      ? new Date(Math.min(...failures.map((f) => f.at.getTime())))
      : null;
  const okTimes = resources
    .map((r) => r.updatedAt)
    .filter((d): d is Date => d !== null);
  const lastOk =
    okTimes.length > 0
      ? new Date(Math.max(...okTimes.map((d) => d.getTime())))
      : null;

  const dashboard = dashboardRes.data;
  const chart = chartRes.data;
  const stats = dashboard?.stats;
  const passCount = watchlistRes.data
    ? watchlistRes.data.rows.filter((r) => r.rules.status === 'pass').length
    : null;
  const sincePct = stats?.since_start_pct ?? null;

  return (
    <div style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}>
      {backendDown && (
        <Banner
          variant="negative"
          role="status"
          actions={
            <button
              type="button"
              onClick={retryFailed}
              style={{
                height: 36,
                padding: '0 var(--sa-space-3)',
                borderRadius: 'var(--sa-radius-md)',
                border: '1px solid var(--sa-negative-border)',
                background: 'transparent',
                color: 'var(--sa-negative-soft)',
                fontSize: 'var(--sa-text-md)',
                fontWeight: 500,
                cursor: 'pointer',
                whiteSpace: 'nowrap',
              }}
            >
              Retry now
            </button>
          }
        >
          <b>
            Can&rsquo;t reach the Stock Trading Agent backend
            {failSince ? ` since ${displayTime(failSince.toISOString())}` : ''}.
          </b>{' '}
          {lastOk
            ? `Showing data from ${displayTime(lastOk.toISOString())}, marked stale. `
            : ''}
          Retrying every 15 seconds. Stops at Alpaca keep protecting positions.
        </Banner>
      )}

      {approvalsRes.failure && (approvalsRes.data?.items.length ?? 0) === 0 && (
        <ErrorBanner
          what="pending approvals"
          failure={approvalsRes.failure}
          updatedAt={approvalsRes.updatedAt}
          onRetry={approvalsRes.reload}
        />
      )}
      {approvalsRes.data && approvalsRes.data.items.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-3)' }}>
          {approvalsRes.failure && (
            <ErrorBanner
              what="pending approvals"
              failure={approvalsRes.failure}
              updatedAt={approvalsRes.updatedAt}
              onRetry={approvalsRes.reload}
            />
          )}
          <CardBoundary name="Pending approvals">
          {approvalsRes.data.items.map((a) => (
            <ApprovalCard
              key={a.id}
              approval={a}
              killSwitchOn={approvalsRes.data?.kill_switch_on ?? false}
              paused={approvalsRes.data?.paused ?? false}
              nextCycle={approvalsRes.data?.next_cycle ?? null}
              entryCutoff={approvalsRes.data?.entry_cutoff ?? null}
              onResolved={() => {
                approvalsRes.reload();
                dashboardRes.reload();
                positionsRes.reload();
              }}
            />
          ))}
          </CardBoundary>
        </div>
      )}

      <NeedsYouCard res={attentionRes} />

      {/* Phone only (item 073): jump links for the ~6,000 px page. */}
      <nav
        aria-label="On this page"
        className="sa-phone-only"
        style={{
          gap: '8px',
          overflowX: 'auto',
          paddingBottom: '2px',
          alignItems: 'center',
        }}
      >
        <span
          style={{
            fontSize: 'var(--sa-text-sm)',
            color: 'var(--sa-text-muted)',
            whiteSpace: 'nowrap',
          }}
        >
          On this page
        </span>
        {(
          [
            ['Positions', '#positions'],
            ['Chart', '#chart'],
            ['Latest decision', '#decision'],
            ['Today', '#today'],
            ['Briefing', '#briefing'],
            ['Watchlist', '#watchlist'],
          ] as const
        ).map(([label, href]) => (
          <a
            key={href}
            href={href}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              height: '36px',
              padding: '0 var(--sa-space-3)',
              borderRadius: '18px',
              border: '1px solid var(--sa-border)',
              background: 'var(--sa-raised)',
              color: 'var(--sa-text-2)',
              fontSize: 'var(--sa-text-md)',
              textDecoration: 'none',
              whiteSpace: 'nowrap',
            }}
          >
            {label}
          </a>
        ))}
      </nav>

      {(dashboard === null ||
        dashboard.cycle_line !== null ||
        dashboard.cycle_counter !== null) && (
        <section
          aria-label="This cycle"
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'stretch',
            gap: '14px 16px',
            padding: '14px 16px',
            background: 'var(--sa-panel)',
            border: '1px solid var(--sa-border)',
            borderRadius: 'var(--sa-radius-lg)',
            fontSize: 'var(--sa-text-md)',
          }}
        >
          <CardBoundary name="Cycle state">
          <CardBody res={dashboardRes} what="the cycle state" skeleton={<SkeletonText lines={1} />}>
            {(d) =>
              d.cycle_line === null && d.cycle_counter === null ? null : (
                <>
                  {d.cycle_line && (
                    <div
                      style={{
                        flex: '1 1 460px',
                        minWidth: 0,
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '12px',
                      }}
                    >
                      <p
                        style={{
                          margin: 0,
                          color: 'var(--sa-text-2)',
                          lineHeight: 1.5,
                        }}
                      >
                        {(() => {
                        const parts = cycleSentenceParts(d.cycle_line);
                        const rest = parts.rest ?? '';
                        const at = rest.indexOf('pre-filter');
                        return (
                          <>
                            <b style={{ fontWeight: 600, color: 'var(--sa-text)' }}>
                              {parts.label}
                            </b>
                            {parts.ny && (
                              <>
                                {' '}
                                <span style={{ color: 'var(--sa-text-muted)' }}>
                                  · {parts.ny}
                                </span>
                              </>
                            )}
                            {parts.rest && ' · '}
                            {at < 0 ? (
                              rest
                            ) : (
                              <>
                                {rest.slice(0, at)}
                                <span
                                  title="The rules check that decides whether the model is asked at all."
                                  style={{
                                    textDecoration: 'underline dotted',
                                    textUnderlineOffset: '3px',
                                    cursor: 'help',
                                  }}
                                >
                                  pre-filter
                                </span>
                                {rest.slice(at + 'pre-filter'.length)}
                              </>
                            )}
                          </>
                        );
                        })()}
                      </p>
                      <CycleValues
                        line={d.cycle_line}
                        callsPerCycle={d.stats.calls_per_cycle}
                      />
                    </div>
                  )}
                  {d.cycle_counter && (
                    <div
                      role="timer"
                      aria-label="Cycle counter"
                      className="sa-cyc-counter"
                      style={{
                        flex: '0 1 340px',
                        minWidth: '260px',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '8px',
                        paddingLeft: '16px',
                        borderLeft: '1px solid var(--sa-border)',
                      }}
                    >
                      <CycleCounter counter={d.cycle_counter} />
                    </div>
                  )}
                </>
              )
            }
          </CardBody>
          </CardBoundary>
        </section>
      )}

      {dashboardRes.failure && (
        <ErrorBanner
          what="the stats"
          failure={dashboardRes.failure}
          updatedAt={dashboardRes.updatedAt}
          onRetry={dashboardRes.reload}
        />
      )}
      {dashboard === null ? (
        dashboardRes.failure === null && (
          <div className="sa-loading sa-tiles sa-tiles-4">
            {[0, 1, 2, 3].map((i) => (
              <SkeletonTile key={i} />
            ))}
          </div>
        )
      ) : (
      <section
        aria-label="Account summary"
        className="sa-tiles sa-tiles-4"
        style={{ opacity: dashboardRes.failure ? 0.55 : 1 }}
      >
        <CardBoundary name="Since start">
        <Link
          to="/performance"
          className="sa-tile"
          aria-label="Since start, open Performance"
          style={{ display: 'flex' }}
        >
          <StatCard
            label="Since start"
            value={sincePct === null ? '—' : signedPct(sincePct)}
            valueColor={
              sincePct === null
                ? undefined
                : sincePct > 0
                  ? 'var(--sa-positive)'
                  : sincePct < 0
                    ? 'var(--sa-negative)'
                    : undefined
            }
            sub={`SPY ${stats?.spy_pct !== null && stats?.spy_pct !== undefined ? signedPct(stats.spy_pct) : '—'} · ${stats?.since_label ?? ''}`}
            footer="Open →"
            style={{ flex: '1 1 auto', width: '100%', height: '100%' }}
          />
        </Link>
        </CardBoundary>
        <CardBoundary name="Positions">
        <Link
          to="/positions"
          className="sa-tile"
          aria-label="Positions, open Positions and orders"
          style={{ display: 'flex' }}
        >
          <StatCard
            label="Positions"
            value={`${stats?.positions ?? 0} of ${stats?.positions_cap ?? '—'}`}
            sub={`cash $${stats?.cash ?? '—'} · no margin`}
            footer="Open →"
            style={{ flex: '1 1 auto', width: '100%', height: '100%' }}
          />
        </Link>
        </CardBoundary>
        <CardBoundary name="Model calls today">
        <Link
          to="/inspector?source=live"
          className="sa-tile"
          aria-label="Model calls, open the LLM inspector"
          title={
            stats
              ? `Decision calls in the shown session: at most ${stats.calls_per_cycle} per cycle, ${stats.cycles_today} cycles.` +
                (stats.reviews_cost_month !== null && stats.reviews_cost_month !== undefined
                  ? ` Reviews have their own $${stats.reviews_cost_cap} monthly cap: $${Number(stats.reviews_cost_month).toFixed(2)} used.`
                  : '') +
                (stats.shadows_cost_month !== null && stats.shadows_cost_month !== undefined
                  ? ` Shadows have their own $${stats.shadows_cost_cap} monthly cap: $${Number(stats.shadows_cost_month).toFixed(2)} used.`
                  : '') +
                (stats.ask_cost_month !== null && stats.ask_cost_month !== undefined
                  ? ` Ask has its own $${stats.ask_cost_cap} monthly cap: $${Number(stats.ask_cost_month).toFixed(2)} used.`
                  : '')
              : undefined
          }
          style={{ display: 'flex' }}
        >
          <StatCard
            label={
              <span
                title="Decision calls in the shown session: at most the per-cycle cap each cycle. Reviews, shadows and Ask have their own monthly caps."
                style={{
                  textDecoration: 'underline dotted',
                  textUnderlineOffset: '3px',
                  cursor: 'help',
                  alignSelf: 'flex-start',
                }}
              >
                {dashboard.session.shown_is_today
                  ? 'Model calls today'
                  : `Model calls · ${sessionDayShort(dashboard.session.shown_date)}`}
              </span>
            }
            value={`${stats?.calls_today ?? 0}`}
            bar={{
              width: `${stats && stats.calls_max > 0 ? Math.min(100, (stats.calls_today / stats.calls_max) * 100) : 0}%`,
              color: 'var(--sa-info)',
            }}
            sub={`of ${stats?.calls_max ?? '—'} max · $${stats ? Number(stats.calls_cost_today).toFixed(3) : '—'} ${dashboard.session.shown_is_today ? 'today' : 'this session'}`}
            footer="Open →"
            style={{ flex: '1 1 auto', width: '100%', height: '100%' }}
          />
        </Link>
        </CardBoundary>
        <CardBoundary name="Last cycle">
        <Link
          to={dashboard.last_cycle ? `/logs?cycle=c-${String(dashboard.last_cycle.cycle_id).padStart(4, '0')}` : '/logs'}
          className="sa-tile"
          aria-label="Last cycle, open Logs"
          title={
            dashboard.last_cycle
              ? `How long the ${formatAmsTime(dashboard.last_cycle.time)} cycle took. Its model calls ran together. Amber above the slow-cycle threshold.`
              : undefined
          }
          style={{ display: 'flex' }}
        >
          <StatCard
            label={
              <span
                title="How long the latest cycle took. Amber above the slow-cycle threshold."
                style={{
                  textDecoration: 'underline dotted',
                  textUnderlineOffset: '3px',
                  cursor: 'help',
                  alignSelf: 'flex-start',
                }}
              >
                Last cycle
              </span>
            }
            value={
              dashboard.last_cycle
                ? formatDuration(dashboard.last_cycle.took_seconds)
                : '—'
            }
            valueColor={
              dashboard.last_cycle?.took_warn ? 'var(--sa-warning-text)' : undefined
            }
            sub={
              dashboard.last_cycle
                ? `${dashboard.last_cycle.calls} call${dashboard.last_cycle.calls === 1 ? '' : 's'}`
                : undefined
            }
            footer="Open →"
            style={{ flex: '1 1 auto', width: '100%', height: '100%' }}
          />
        </Link>
        </CardBoundary>
      </section>
      )}

      <div
        className="sa-card-row"
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'flex-start',
          gap: 'var(--sa-space-5)',
        }}
      >
        <span id="positions" style={{ display: 'block', height: 0, scrollMarginTop: '16px' }} />
        <OpenPositionsMini
          res={positionsRes}
          nextCycle={dashboard?.session.next_cycle ?? null}
          nextCycleToday={dashboard?.cycle_counter?.next_cycle_today !== false}
          passCount={passCount}
        />
        <RiskBudgetCard
          res={budgetRes}
          reentryBlocked={positionsRes.data?.today.reentry_blocked ?? []}
        />
      </div>

      {dashboard?.market_closed && (
        <MarketClosed
          mc={dashboard.market_closed}
          day={
            dashboard.session.shown_is_today
              ? undefined
              : dashboard.session.shown_date
          }
        />
      )}

      <div
        className="sa-card-row"
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'flex-start',
          gap: 'var(--sa-space-5)',
        }}
      >
        <span id="chart" style={{ display: 'block', height: 0, scrollMarginTop: '16px' }} />
        <div style={{ flex: '999 1 600px', minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <Card
            ariaLabel={chartSymbol ? `Chart for ${chartSymbol}` : 'Chart'}
            busy={chartRes.loading}
            refreshing={chartRes.refreshing}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'baseline',
                gap: '8px 16px',
                flexWrap: 'wrap',
              }}
            >
              <h2 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>
                {chartSymbol ?? 'Chart'}
              </h2>
              {chart && (
                <>
                  <span
                    style={{
                      fontFamily: 'var(--sa-font-mono)',
                      fontSize: 'var(--sa-text-price)',
                      fontWeight: 500,
                    }}
                  >
                    ${formatPrice(chart.price)}
                  </span>
                  <span
                    style={{
                      fontFamily: 'var(--sa-font-mono)',
                      fontSize: 'var(--sa-text-base)',
                      color:
                        chart.day_change_pct !== null && chart.day_change_pct >= 0
                          ? 'var(--sa-positive)'
                          : 'var(--sa-negative-text)',
                    }}
                  >
                    {formatDayChange(chart.day_change_pct)} today
                  </span>
                  {chart.warming_up && <Chip variant="info">Warming up</Chip>}
                </>
              )}
              <span style={{ flex: 1 }} />
              <div
                role="group"
                aria-label="Pick a symbol"
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: '6px',
                  alignItems: 'center',
                }}
              >
                {heldSymbols.map((symbol) => (
                  <button
                    key={symbol}
                    type="button"
                    aria-pressed={symbol === chartSymbol}
                    onClick={() => pickSymbol(symbol)}
                    style={{
                      height: '36px',
                      padding: '0 var(--sa-space-3)',
                      borderRadius: 'var(--sa-radius-md)',
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '6px',
                      fontSize: 'var(--sa-text-md)',
                      fontWeight: 600,
                      cursor: 'pointer',
                      border: `1px solid ${symbol === chartSymbol ? 'var(--sa-accent)' : 'var(--sa-border-strong)'}`,
                      background:
                        symbol === chartSymbol
                          ? 'var(--sa-accent-bg)'
                          : 'var(--sa-raised)',
                      color: 'var(--sa-text)',
                    }}
                  >
                    {symbol}
                    <span
                      style={{ fontSize: 'var(--sa-text-xs)', color: 'var(--sa-info)' }}
                    >
                      held
                    </span>
                  </button>
                ))}
                <button
                  type="button"
                  aria-haspopup="listbox"
                  onClick={() => setPickerOpen(true)}
                  style={{
                    height: '36px',
                    padding: '0 var(--sa-space-3)',
                    borderRadius: 'var(--sa-radius-md)',
                    border: '1px solid var(--sa-border-strong)',
                    background: 'var(--sa-bg)',
                    color: 'var(--sa-text-muted)',
                    fontSize: 'var(--sa-text-md)',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 'var(--sa-space-2)',
                    cursor: 'pointer',
                  }}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: '0 0 14px' }}>
                    <circle cx="11" cy="11" r="6" />
                    <line x1="16" y1="16" x2="21" y2="21" />
                  </svg>
                  Find symbol
                </button>
              </div>
            </div>
            <div
              style={{
                fontSize: 'var(--sa-text-sm)',
                color: 'var(--sa-text-muted)',
                marginTop: '-4px',
              }}
            >
              1h bars · last 5 sessions · SIP, 15 min delayed · or pick a row in
              the watchlist
            </div>
            <CardBody
              res={chartRes}
              what={chartSymbol ? `the chart for ${chartSymbol}` : 'the chart'}
              isEmpty={(d) => d === null || d.bars.length === 0}
              skeleton={<SkeletonChart height={320} />}
              empty={
                <div style={{ height: '320px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--sa-text-muted)' }}>
                  {chartSymbol
                    ? `No bars loaded for ${chartSymbol} yet — bars appear after its first data pull.`
                    : 'Pick a symbol to chart.'}
                </div>
              }
            >
              {(d) => d !== null && <PriceChart bars={d.bars} levels={d.levels} />}
            </CardBody>
            <div style={{ display: 'flex', gap: '8px 16px', flexWrap: 'wrap', marginTop: '18px', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
              <Legend kind="bar" swatch="var(--sa-chart-ema20)" label="EMA 20" />
              <Legend kind="bar" swatch="var(--sa-chart-ema50)" label="EMA 50" />
              <Legend kind="dashed" swatch="var(--sa-chart-vwap)" width={2} label="Session VWAP" />
              {chart?.levels.entry && <Legend kind="dashed" swatch="var(--sa-text-2)" label="Entry" />}
              {chart?.levels.stop && <Legend kind="line" swatch="var(--sa-negative)" label="Stop" />}
              {chart?.levels.target && <Legend kind="line" swatch="var(--sa-positive)" label="Target" />}
            </div>
          </Card>
        </div>
        <span id="decision" style={{ display: 'block', height: 0, scrollMarginTop: '16px' }} />
        <div style={{ flex: '1 1 340px', minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <LatestDecision res={dashboardRes} />
        </div>
      </div>

      <div
        className="sa-card-row"
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'flex-start',
          gap: 'var(--sa-space-5)',
        }}
      >
        <span id="today" style={{ display: 'block', height: 0, scrollMarginTop: '16px' }} />
        <div style={{ flex: '1 1 420px', minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <Feed res={dashboardRes} />
        </div>
        <span id="briefing" style={{ display: 'block', height: 0, scrollMarginTop: '16px' }} />
        <div style={{ flex: '1 1 420px', minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <BriefingCard res={dashboardRes} />
        </div>
      </div>

      <span id="watchlist" style={{ display: 'block', height: 0, scrollMarginTop: '16px' }} />
      <Watchlist
        res={watchlistRes}
        selected={chartSymbol}
        onSelect={pickSymbol}
      />

      <FindSymbolDialog
        open={pickerOpen}
        symbols={pickerSymbols}
        held={heldSet}
        selected={chartSymbol}
        onPick={(symbol) => {
          pickSymbol(symbol);
          setPickerOpen(false);
        }}
        onClose={() => setPickerOpen(false)}
      />
    </div>
  );
}

function Legend({
  kind,
  swatch,
  width,
  label,
}: {
  kind: 'bar' | 'line' | 'dashed';
  swatch: string;
  width?: number;
  label: string;
}) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
      <span
        style={
          kind === 'bar'
            ? { width: '14px', height: '2px', background: swatch }
            : {
                width: '14px',
                height: 0,
                borderTop: `${width ?? 1}px ${kind === 'dashed' ? 'dashed' : 'solid'} ${swatch}`,
              }
        }
      />
      {label}
    </span>
  );
}
