import { useCallback, useEffect, useState } from 'react';
import { Link, useLocation, useSearchParams } from 'react-router-dom';

import { useLiveUpdates } from '../api/liveContext';
import { ApiError } from '../api/client';
import { RecordNotFound } from '../components/errorPages';
import {
  getReview,
  getReviews,
  getVerdicts,
  postVerdict,
} from '../api/state';
import { Card } from '../components/Card';
import { Chip } from '../components/Chip';
import { VerdictButtons } from '../components/VerdictButtons';
import type {
  BriefingFacts,
  ReviewDetail,
  ReviewListItem,
  VerdictItem,
  VerdictValue,
  WeeklyFacts,
} from '../types/api';
import {
  addDays,
  dayLabel,
  decidedLabel,
  metaLine,
  numVariant,
  signedPct,
  signedPts,
  signedR,
  upcomingWeek,
  usd,
  verdictLabel,
  verdictOutcomeText,
  verdictVariant,
  weekLabel,
} from './reviewsFormat';

const WEEKLY_EXPLAINER =
  'Written every Saturday 10:00 Amsterdam, covering Monday to Friday.';
const BRIEFING_EXPLAINER =
  'One card per trading day, written 09:00 New York with open positions and ' +
  'stop distance, earnings today or tomorrow, the market regime, ' +
  "yesterday's result and active banners, at most 80 words.";

const tileColor = {
  positive: 'var(--sa-positive)',
  negative: 'var(--sa-negative)',
  neutral: 'var(--sa-text)',
} as const;

function StatTile({
  label,
  value,
  sub,
  variant = 'neutral',
}: {
  label: string;
  value: string;
  sub: string;
  variant?: 'positive' | 'negative' | 'neutral';
}) {
  return (
    <div
      style={{
        flex: '1 1 150px',
        padding: 'var(--sa-space-3) var(--sa-space-4)',
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
          fontFamily: 'var(--sa-font-mono)',
          fontSize: 'var(--sa-text-2xl)',
          color: tileColor[variant],
          whiteSpace: 'nowrap',
        }}
      >
        {value}
      </span>
      <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
        {sub}
      </span>
    </div>
  );
}

function MetaLine({ review }: { review: ReviewListItem }) {
  return (
    <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
      {metaLine(review)}
    </span>
  );
}

// ---------------------------------------------------------------------------
// weekly review

function WeeklyView({ review }: { review: ReviewDetail }) {
  const facts = review.facts as WeeklyFacts | null;
  const content = review.content;
  if (!facts) {
    return (
      <Card>
        <p style={{ margin: 0, lineHeight: 1.6, color: 'var(--sa-text-2)' }}>
          {review.text}
        </p>
      </Card>
    );
  }
  const trades = [...(facts.trades ?? [])].sort(
    (a, b) => (b.r ?? -Infinity) - (a.r ?? -Infinity),
  );
  const skips = facts.skips;
  const miss = skips?.biggest_miss;
  const flatPct = facts.limits?.skip_flat_pct ?? 2;
  const nSessions = facts.limits?.verdict_after_sessions ?? 5;

  return (
    <>
      <Card>
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'baseline',
            justifyContent: 'space-between',
            gap: 'var(--sa-space-2)',
          }}
        >
          <h2 style={{ margin: 0, fontSize: 'var(--sa-text-2xl)', fontWeight: 600 }}>
            {weekLabel(facts)}
          </h2>
          <MetaLine review={review} />
        </div>
        <p
          style={{
            margin: 0,
            maxWidth: '760px',
            lineHeight: 1.6,
            color: 'var(--sa-text-2)',
          }}
        >
          {content?.summary ?? review.text}
        </p>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-2)' }}>
          <StatTile
            label="Return"
            value={signedPct(facts.return_pct)}
            sub={`${usd(facts.equity_start)} → ${usd(facts.equity_end)}`}
            variant={numVariant(facts.return_pct)}
          />
          <StatTile
            label="vs holding SPY"
            value={signedPts(facts.vs_spy_pts)}
            sub={`SPY ${signedPct(facts.spy_return_pct)}`}
            variant={numVariant(facts.vs_spy_pts)}
          />
          <StatTile
            label="Trades closed"
            value={String(facts.trades_closed)}
            sub={`${facts.won} won · ${facts.lost} lost`}
          />
          <StatTile
            label="Average R"
            value={signedR(facts.avg_r)}
            sub="per closed trade"
            variant={numVariant(facts.avg_r)}
          />
          <StatTile
            label="Max drawdown"
            value={signedPct(facts.max_drawdown_pct)}
            sub="close to close"
            variant={facts.max_drawdown_pct ? 'negative' : 'neutral'}
          />
          <StatTile
            label="Model cost"
            value={usd(facts.model_cost_usd)}
            sub={`${facts.model_calls} calls · reviews ${usd(facts.reviews_cost_usd)}`}
          />
        </div>
      </Card>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-5)' }}>
        <div style={{ flex: '1 1 380px', minWidth: 0 }}>
          <Card label="Trades closed" right={<span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>best first</span>}>
            {trades.length === 0 ? (
              <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
                No trades closed this week.
              </div>
            ) : (
              trades.map((t, i) => (
                <div
                  key={`${t.symbol}-${i}`}
                  style={{
                    display: 'grid',
                    gridTemplateColumns: '64px 1fr auto',
                    gap: 'var(--sa-space-3)',
                    alignItems: 'baseline',
                    padding: 'var(--sa-space-3) 0',
                    borderBottom:
                      i < trades.length - 1
                        ? '1px solid var(--sa-border)'
                        : 'none',
                    fontSize: 'var(--sa-text-md)',
                  }}
                >
                  <b style={{ fontWeight: 600 }}>{t.symbol}</b>
                  <span style={{ color: 'var(--sa-text-2)' }}>
                    {dayLabel(t.opened)} → {dayLabel(t.closed)}{' '}
                    <span style={{ color: 'var(--sa-text-muted)' }}>· {t.exit}</span>
                  </span>
                  <span
                    style={{
                      fontFamily: 'var(--sa-font-mono)',
                      color: tileColor[numVariant(t.r)],
                    }}
                  >
                    {signedR(t.r)}
                  </span>
                </div>
              ))
            )}
          </Card>
        </div>
        <div style={{ flex: '1 1 380px', minWidth: 0 }}>
          <Card
            label={`Skipped setups after ${facts.limits?.verdict_after_sessions ?? 5} sessions`}
            right={
              skips && skips.pending > 0 ? (
                <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
                  {skips.pending} still pending
                </span>
              ) : undefined
            }
          >
            {skips && skips.total > 0 ? (
              <>
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
                    gap: 'var(--sa-space-2)',
                  }}
                >
                  {[
                    { n: skips.up, label: `up more than ${flatPct}%`, variant: 'positive' as const },
                    { n: skips.flat, label: `within ±${flatPct}%`, variant: 'neutral' as const },
                    { n: skips.down, label: `down more than ${flatPct}%`, variant: 'negative' as const },
                  ].map((cell) => (
                    <div
                      key={cell.label}
                      style={{
                        padding: 'var(--sa-space-3)',
                        border: '1px solid var(--sa-border)',
                        borderRadius: 'var(--sa-radius-md)',
                      }}
                    >
                      <div
                        style={{
                          fontFamily: 'var(--sa-font-mono)',
                          fontSize: 'var(--sa-text-2xl)',
                          color: tileColor[cell.variant],
                        }}
                      >
                        {cell.n}
                      </div>
                      <div
                        style={{
                          fontSize: 'var(--sa-text-sm)',
                          color: 'var(--sa-text-muted)',
                        }}
                      >
                        {cell.label}
                      </div>
                    </div>
                  ))}
                </div>
                <div
                  style={{
                    fontSize: 'var(--sa-text-md)',
                    color: 'var(--sa-text-2)',
                    lineHeight: 1.5,
                  }}
                >
                  {skips.with_outcome} of {skips.total} model skips have a{' '}
                  {nSessions}-session result.
                  {miss && (
                    <>
                      {' '}
                      Biggest miss: <b style={{ fontWeight: 600 }}>{miss.symbol}</b>{' '}
                      skipped {dayLabel(miss.date)}
                      {miss.confidence != null &&
                        ` at confidence ${miss.confidence.toFixed(2)}`}
                      , then rose {miss.pct}%.
                    </>
                  )}
                </div>
              </>
            ) : (
              <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
                No model skips this week.
              </div>
            )}
          </Card>
        </div>
      </div>

      {content?.observations && content.observations.length > 0 && (
        <Card label="Observations">
          <ul
            style={{
              margin: 0,
              paddingLeft: 'var(--sa-space-5)',
              display: 'flex',
              flexDirection: 'column',
              gap: 'var(--sa-space-2)',
              lineHeight: 1.55,
              color: 'var(--sa-text-2)',
            }}
          >
            {content.observations.map((o, i) => (
              <li key={i}>{o}</li>
            ))}
          </ul>
        </Card>
      )}

      {content?.suggestion && (
        <section
          aria-label="Suggestion"
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            gap: 'var(--sa-space-3) var(--sa-space-5)',
            padding: 'var(--sa-space-4) var(--sa-space-5)',
            border: '1px solid var(--sa-accent-border)',
            background: 'var(--sa-accent-bg)',
            borderRadius: 'var(--sa-radius-lg)',
          }}
        >
          <div
            style={{
              flex: '1 1 480px',
              display: 'flex',
              flexDirection: 'column',
              gap: '4px',
            }}
          >
            <div
              style={{
                fontSize: 'var(--sa-text-sm)',
                fontWeight: 600,
                color: 'var(--sa-accent)',
                textTransform: 'uppercase',
                letterSpacing: '0.08em',
              }}
            >
              Suggestion · not applied
            </div>
            <div style={{ lineHeight: 1.55 }}>{content.suggestion}</div>
          </div>
          <Link
            to="/performance"
            style={{
              height: 'var(--sa-control-height)',
              padding: '0 var(--sa-space-4)',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 'var(--sa-space-2)',
              borderRadius: 'var(--sa-radius-md)',
              border: '1px solid var(--sa-border-strong)',
              background: 'var(--sa-raised)',
              color: 'var(--sa-text)',
              fontSize: 'var(--sa-text-base)',
              fontWeight: 600,
              textDecoration: 'none',
              whiteSpace: 'nowrap',
              boxSizing: 'border-box',
            }}
          >
            Open Performance
          </Link>
        </section>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// daily briefing

function BriefingView({ review }: { review: ReviewDetail }) {
  const facts = review.facts as BriefingFacts | null;
  return (
    <Card>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          gap: 'var(--sa-space-2)',
        }}
      >
        <h2 style={{ margin: 0, fontSize: 'var(--sa-text-2xl)', fontWeight: 600 }}>
          {review.label}
        </h2>
        <MetaLine review={review} />
      </div>
      <p
        style={{
          margin: 0,
          maxWidth: '760px',
          lineHeight: 1.6,
          color: 'var(--sa-text-2)',
        }}
      >
        {review.content?.text ?? review.text}
      </p>
      {facts && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-2)' }}>
          <StatTile
            label="Open positions"
            value={String(facts.positions?.length ?? 0)}
            sub={
              facts.positions
                ?.map((p) =>
                  p.stop_distance_pct != null
                    ? `${p.symbol} stop ${p.stop_distance_pct}% below`
                    : `${p.symbol} no fresh price`,
                )
                .join(' · ') || 'none held'
            }
          />
          <StatTile
            label="Earnings today / tomorrow"
            value={`${facts.earnings_today?.length ?? 0} / ${facts.earnings_tomorrow?.length ?? 0}`}
            sub={
              [
                ...(facts.earnings_today?.map((e) => `${e.symbol} today`) ?? []),
                ...(facts.earnings_tomorrow?.map((e) => `${e.symbol} tomorrow`) ?? []),
              ].join(' · ') || 'none'
            }
          />
          <StatTile label="Regime" value={facts.regime ?? '—'} sub="from SPY" />
          <StatTile
            label="Yesterday"
            value={signedPct(facts.yesterday?.return_pct)}
            sub={
              facts.yesterday?.return_usd != null
                ? `${facts.yesterday.return_usd >= 0 ? '+' : '-'}$${Math.abs(facts.yesterday.return_usd).toFixed(2)}`
                : 'no equity snapshots'
            }
            variant={numVariant(facts.yesterday?.return_pct)}
          />
          <StatTile
            label="Active banners"
            value={String(facts.banners?.length ?? 0)}
            sub={
              facts.banners?.map((b) => b.kind.replace(/_/g, ' ')).join(' · ') ||
              'none'
            }
          />
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// verdicts to confirm

function VerdictsCard({
  items,
  onResolve,
}: {
  items: VerdictItem[];
  onResolve: (id: number, v: VerdictValue) => void;
}) {
  const location = useLocation();
  return (
    <Card
      label="Verdicts to confirm"
      right={
        <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          {items.length} waiting
        </span>
      }
    >
      {items.length === 0 ? (
        <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          Nothing waiting — a draft is written once a decision's 5-session
          result is in.
        </div>
      ) : (
        items.map((v, i) => (
          <div
            key={v.id}
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              alignItems: 'center',
              gap: 'var(--sa-space-2) var(--sa-space-4)',
              padding: 'var(--sa-space-3) 0',
              borderBottom:
                i < items.length - 1 ? '1px solid var(--sa-border)' : 'none',
            }}
          >
            <div
              style={{
                flex: '1 1 320px',
                minWidth: 0,
                display: 'flex',
                flexDirection: 'column',
                gap: '4px',
              }}
            >
              <div style={{ fontSize: 'var(--sa-text-md)' }}>
                <Link
                  to={`/decisions/${v.decision_id}`}
                  state={{ backgroundLocation: location }}
                  style={{ fontWeight: 600, color: 'var(--sa-text)', textDecoration: 'none' }}
                >
                  {v.symbol}
                </Link>{' '}
                <span
                  style={{
                    fontFamily: 'var(--sa-font-mono)',
                    fontSize: 'var(--sa-text-sm)',
                    color: 'var(--sa-text-2)',
                  }}
                >
                  {v.final_action.toUpperCase()}
                </span>{' '}
                <span style={{ color: 'var(--sa-text-muted)' }}>
                  · {decidedLabel(v.decided_at)} · {verdictOutcomeText(v.outcome)}
                </span>
              </div>
              <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>
                Draft:{' '}
                <Chip variant={verdictVariant(v.verdict)}>
                  {verdictLabel(v.verdict)}
                </Chip>{' '}
                <span style={{ color: 'var(--sa-text-muted)' }}>{v.reason}</span>
              </div>
            </div>
            <VerdictButtons
              current={v.verdict}
              onPick={(value) => onResolve(v.id, value)}
            />
          </div>
        ))
      )}
      <div
        style={{
          fontSize: 'var(--sa-text-sm)',
          color: 'var(--sa-text-muted)',
          paddingTop: '4px',
        }}
      >
        Drafts are written once the 5-session result is in. Only verdicts you
        confirm count in reviews.
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// page

export default function Reviews() {
  const [tab, setTab] = useState<'Weekly' | 'Briefings'>('Weekly');
  const kind = tab === 'Weekly' ? 'weekly' : 'briefing';
  const [items, setItems] = useState<ReviewListItem[]>([]);
  const [nextDue, setNextDue] = useState<string | null>(null);
  const [schedule, setSchedule] = useState<string | null>(null);
  // The selection lives in the URL (?review=<id>) so a review is a
  // deep-linked record: an id that doesn't exist gets its own
  // not-found page (docs/SPEC.md → Not found, a record).
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedParam = searchParams.get('review');
  // The newest review auto-opens; an explicit ?review=<id> always wins —
  // even when it isn't in the list, where its detail fetch answers the
  // record's not-found page. The param is never written by the list
  // refresh, so a stale response can't resurrect an old selection.
  const selectedId =
    selectedParam !== null
      ? Number(selectedParam)
      : (items[0]?.id ?? null);
  const selectReview = useCallback(
    (id: number | null) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (id === null) next.delete('review');
          else next.set('review', String(id));
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );
  const [detail, setDetail] = useState<ReviewDetail | null>(null);
  const [missingId, setMissingId] = useState<number | null>(null);
  const [verdicts, setVerdicts] = useState<VerdictItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    getReviews(kind)
      .then((r) => {
        setItems(r.items);
        setNextDue(r.next_weekly_due);
        setSchedule(r.schedule);
        setError(null);
      })
      .catch(() => setError('Reviews could not be loaded.'));
    getVerdicts('draft')
      .then((r) => setVerdicts(r.items))
      .catch(() => setVerdicts([]));
  }, [kind]);

  const [prevKind, setPrevKind] = useState(kind);
  if (kind !== prevKind) {
    setPrevKind(kind);
    setDetail(null);
    setMissingId(null);
  }
  useEffect(() => {
    refresh();
  }, [refresh]);
  useLiveUpdates(['reviews'], refresh);

  const [prevSelectedId, setPrevSelectedId] = useState(selectedId);
  if (selectedId !== prevSelectedId) {
    setPrevSelectedId(selectedId);
    setDetail(null);
    setMissingId(null);
  }
  useEffect(() => {
    if (selectedId === null) return;
    getReview(selectedId)
      .then(setDetail)
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 404) {
          setMissingId(selectedId);
        } else {
          setDetail(null);
        }
      });
  }, [selectedId]);

  const resolve = useCallback(
    (id: number, value: VerdictValue) => {
      postVerdict(id, value)
        .then(() => getVerdicts('draft'))
        .then((r) => setVerdicts(r.items))
        .catch(console.error);
    },
    [],
  );

  // The pending row is the first Saturday whose week has no review yet;
  // a week already written (catch-up) rolls forward seven days.
  let upcoming: ReturnType<typeof upcomingWeek> | null = null;
  if (kind === 'weekly' && nextDue) {
    let due = nextDue;
    for (let i = 0; i < 52; i += 1) {
      const candidate = upcomingWeek(due);
      if (!items.some((item) => item.period === candidate.period)) {
        upcoming = candidate;
        break;
      }
      due = `${addDays(due.slice(0, 10), 7)}T${due.slice(11) || '00:00:00'}`;
    }
  }

  return (
    <div
      style={{
        maxWidth: '1440px',
        width: '100%',
        margin: '0 auto',
        padding: 'var(--sa-space-6)',
        boxSizing: 'border-box',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sa-space-5)',
      }}
    >
      {error && (
        <div
          style={{
            padding: 'var(--sa-space-3) var(--sa-space-4)',
            border: '1px solid var(--sa-negative-border)',
            borderRadius: 'var(--sa-radius-md)',
            background: 'var(--sa-negative-bg)',
            color: 'var(--sa-negative-soft)',
          }}
        >
          {error}
        </div>
      )}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'baseline',
          gap: 'var(--sa-space-3)',
        }}
      >
        {schedule && (
          <span
            style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}
          >
            {schedule}
          </span>
        )}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-5)', alignItems: 'flex-start' }}>
        <section
          aria-label="Reviews list"
          style={{
            flex: '1 1 260px',
            maxWidth: '300px',
            background: 'var(--sa-panel)',
            border: '1px solid var(--sa-border)',
            borderRadius: 'var(--sa-radius-lg)',
            padding: 'var(--sa-space-3)',
            display: 'flex',
            flexDirection: 'column',
            gap: '4px',
            boxSizing: 'border-box',
          }}
        >
          {/* Real tabs (design/screens/Reviews.dc.html): arrow keys move
           * between tabs, roving tabindex keeps one tab in the Tab order. */}
          <div
            role="tablist"
            aria-label="Review kind"
            onKeyDown={(e) => {
              if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
              e.preventDefault();
              const tabs = Array.from(
                e.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]'),
              );
              const i = tabs.indexOf(document.activeElement as HTMLElement);
              const next =
                i < 0
                  ? 0
                  : (i + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) %
                    tabs.length;
              tabs[next].focus();
              tabs[next].click();
            }}
            style={{
              display: 'inline-flex',
              background: 'var(--sa-raised)',
              border: '1px solid var(--sa-border)',
              borderRadius: 'var(--sa-radius-md)',
              padding: '2px',
            }}
          >
            {(['Weekly', 'Briefings'] as const).map((option) => (
              <button
                key={option}
                type="button"
                role="tab"
                aria-selected={option === tab}
                tabIndex={option === tab ? 0 : -1}
                onClick={() => {
                  setTab(option);
                  selectReview(null);
                }}
                style={{
                  height: '32px',
                  padding: '0 var(--sa-space-4)',
                  borderRadius: 'var(--sa-radius-sm)',
                  background: option === tab ? 'var(--sa-panel)' : 'transparent',
                  border: 'none',
                  color: option === tab ? 'var(--sa-text)' : 'var(--sa-text-muted)',
                  fontSize: 'var(--sa-text-sm)',
                  fontWeight: 500,
                  cursor: 'pointer',
                }}
              >
                {option}
              </button>
            ))}
          </div>
          {upcoming && (
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                minHeight: '52px',
                padding: 'var(--sa-space-2) var(--sa-space-3)',
                color: 'var(--sa-text-muted)',
                fontSize: 'var(--sa-text-md)',
              }}
            >
              <span style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                <span style={{ fontWeight: 600 }}>{upcoming.label}</span>
                <span style={{ fontSize: 'var(--sa-text-sm)' }}>{upcoming.sub}</span>
              </span>
              <span style={{ fontSize: 'var(--sa-text-sm)' }}>{upcoming.due}</span>
            </div>
          )}
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => selectReview(item.id)}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: 'var(--sa-space-2)',
                minHeight: '52px',
                padding: 'var(--sa-space-2) var(--sa-space-3)',
                borderRadius: 'var(--sa-radius-sm)',
                border: 'none',
                background:
                  item.id === selectedId ? 'var(--sa-accent-bg)' : 'transparent',
                boxShadow:
                  item.id === selectedId
                    ? 'inset 3px 0 0 var(--sa-accent)'
                    : 'none',
                color: 'var(--sa-text)',
                textDecoration: 'none',
                cursor: 'pointer',
                fontFamily: 'inherit',
                fontSize: 'var(--sa-text-md)',
                textAlign: 'left',
              }}
            >
              <span style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                <span style={{ fontWeight: 600, fontSize: 'var(--sa-text-base)' }}>
                  {item.label}
                </span>
                <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                  {item.sub}
                </span>
                {item.preview && (
                  <span
                    style={{
                      fontSize: 'var(--sa-text-sm)',
                      color: 'var(--sa-text-muted)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      maxWidth: '190px',
                    }}
                  >
                    {item.preview}
                  </span>
                )}
              </span>
              {item.kind === 'weekly' && item.return_pct != null && (
                <span
                  style={{
                    fontFamily: 'var(--sa-font-mono)',
                    fontSize: 'var(--sa-text-sm)',
                    color: tileColor[numVariant(item.return_pct)],
                  }}
                >
                  {signedPct(item.return_pct)}
                </span>
              )}
            </button>
          ))}
          {items.length === 0 && (
            <div
              style={{
                padding: 'var(--sa-space-3)',
                fontSize: 'var(--sa-text-md)',
                color: 'var(--sa-text-muted)',
              }}
            >
              {kind === 'weekly'
                ? 'No weekly reviews yet.'
                : 'No briefings yet.'}
            </div>
          )}
          <div style={{ padding: 'var(--sa-space-2) var(--sa-space-1) 0' }}>
            <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
              {kind === 'weekly' ? WEEKLY_EXPLAINER : BRIEFING_EXPLAINER}
            </div>
          </div>
        </section>

        <div
          style={{
            flex: '999 1 560px',
            minWidth: 0,
            display: 'flex',
            flexDirection: 'column',
            gap: 'var(--sa-space-5)',
          }}
        >
          {missingId !== null && selectedId === missingId ? (
            <RecordNotFound
              what={`Review ${missingId} doesn't exist`}
              why="Reviews are never deleted within a run."
              searchTo="/inspector"
              searchLabel="Search the LLM inspector"
            />
          ) : detail ? (
            detail.kind === 'weekly' ? (
              <WeeklyView review={detail} />
            ) : (
              <BriefingView review={detail} />
            )
          ) : (
            <Card>
              <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
                {items.length === 0
                  ? kind === 'weekly'
                    ? 'The first weekly review is written Saturday 10:00 Amsterdam time.'
                    : 'Briefings are written at 09:00 New York on trading days.'
                  : 'Select a review on the left.'}
              </div>
            </Card>
          )}
          <VerdictsCard items={verdicts} onResolve={resolve} />
        </div>
      </div>
    </div>
  );
}
