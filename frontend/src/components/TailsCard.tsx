/** "Winners and tails" card on Performance (design: Performance.dc.html).
 *
 * Shows whether the run leans on a few large winners and whether fixed
 * targets cut them off — R distribution, tail tiles, after-exit medians
 * and the per-trade table. Measures only.
 */

import type { PerformanceTailsResponse, TailsTradeRow } from '../types/api';
import { Card } from './Card';
import { CardBody } from './cardState';
import type { Resource } from '../api/useResource';

const mono: React.CSSProperties = {
  fontFamily: 'var(--sa-font-mono)',
};
const muted = 'var(--sa-text-muted)';
const positive = 'var(--sa-positive)';
const negative = 'var(--sa-negative-text)';

/** "+1.6R" / "−1.05R" — up to two decimals, trimmed. */
function r(value: string | null | undefined, digits = 2): string {
  if (value == null) return '—';
  const n = Number(value);
  const abs = Math.abs(n).toFixed(digits).replace(/0+$/, '').replace(/\.$/, '');
  if (n > 0) return `+${abs}R`;
  if (n < 0) return `−${abs}R`;
  return `${abs}R`;
}

function rColor(value: string | null | undefined): string {
  if (value == null) return 'var(--sa-text)';
  const n = Number(value);
  if (n > 0) return positive;
  if (n < 0) return negative;
  return 'var(--sa-text)';
}

function money(value: string | null | undefined): string {
  if (value == null) return '—';
  const n = Number(value);
  const abs = `$${Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return n > 0 ? `+${abs}` : n < 0 ? `−${abs}` : abs;
}

/** "8 Oct" in Amsterdam time. */
function day(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    timeZone: 'Europe/Amsterdam',
  });
}

/** Bucket x-axis label: "< −1.5", "−1.5", "0", "≥ 3". */
function bucketLabel(lower: string | null, upper: string | null): string {
  const trim = (v: string) =>
    Math.abs(Number(v)).toString().replace(/\.0+$/, '');
  if (lower == null) return `< −${trim(upper ?? '')}`;
  if (upper == null) return `≥ ${trim(lower)}`;
  const n = Number(lower);
  return n < 0 ? `−${trim(lower)}` : trim(lower);
}

function Tile({
  title,
  label,
  value,
  valueColor,
  sub,
}: {
  title: string;
  label: string;
  value: string;
  valueColor?: string;
  sub: string;
}) {
  return (
    <div
      style={{
        flex: '1 1 170px',
        minWidth: 0,
        padding: '12px 14px',
        border: '1px solid var(--sa-border)',
        borderRadius: 'var(--sa-radius-md)',
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
      }}
    >
      <span
        title={title}
        style={{
          fontSize: 'var(--sa-text-sm)',
          color: muted,
          textDecoration: 'underline dotted',
          textUnderlineOffset: '3px',
          cursor: 'help',
          alignSelf: 'flex-start',
        }}
      >
        {label}
      </span>
      <span style={{ ...mono, fontSize: '20px', color: valueColor ?? 'var(--sa-text)' }}>
        {value}
      </span>
      <span style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>{sub}</span>
    </div>
  );
}

function Row({ trade }: { trade: TailsTradeRow }) {
  const pending = trade.after_exit_status === 'pending';
  const cell = (v: string | null, pendingText: boolean) =>
    pending && pendingText ? (
      <span style={{ color: muted }}>pending</span>
    ) : (
      <span style={{ color: rColor(v) }}>{r(v, 1)}</span>
    );
  return (
    <tr style={{ borderBottom: '1px solid var(--sa-border)' }}>
      <td style={{ padding: '10px', fontWeight: 600 }}>{trade.symbol}</td>
      <td style={{ padding: '10px', fontSize: 'var(--sa-text-md)' }}>
        {trade.exit_label}
      </td>
      <td style={{ padding: '10px', textAlign: 'right', ...mono }}>
        <span style={{ color: rColor(trade.result_r) }}>{r(trade.result_r, 1)}</span>
      </td>
      <td style={{ padding: '10px', textAlign: 'right', ...mono }}>
        {r(trade.best_open_r, 1)}
      </td>
      <td style={{ padding: '10px', textAlign: 'right', ...mono }}>
        {cell(trade.after_best_r, true)}
      </td>
      <td style={{ padding: '10px', textAlign: 'right', ...mono }}>
        {cell(trade.after_close_r, true)}
      </td>
    </tr>
  );
}

export function TailsCard({ res }: { res: Resource<PerformanceTailsResponse> }) {
  return (
    <Card label="Winners and tails" right="current run · adjusted · closed trades">
      <CardBody res={res} what="winners and tails">
        {(data) => <Tails data={data} />}
      </CardBody>
    </Card>
  );
}

function Tails({ data }: { data: PerformanceTailsResponse }) {
  const sessions = data.after_exit_sessions ?? 5;
  const topPct = data.top_share_pct ?? 20;
  const maxCount = Math.max(1, ...data.distribution.map((b) => b.count));
  const labelled = data.distribution
    .filter((b) => b.count > 0)
    .map((b) => {
      const l = bucketLabel(b.lower, b.upper).replace(/^(< |≥ )/, '');
      if (b.lower == null) return `${b.count} below −1.5`;
      if (b.upper == null) return `${b.count} at ${l} or more`;
      return `${b.count} between ${l} and ${bucketLabel(b.upper, null).replace('≥ ', '')}`;
    })
    .join(', ');

  const exitLabel = (reason: string | null): string =>
    ({ target: 'target', stop: 'stop', exit: 'agent exit' }[reason ?? ''] ??
    reason ??
    '—');

  const afterTiles = data.after_exit.flatMap((g) => {
    const tiles: React.ReactElement[] = [];
    const name = `${g.label === 'agent exit' ? 'Agent' : g.label[0].toUpperCase() + g.label.slice(1)} exits`;
    if (g.reason === 'target') {
      const n1 = Math.round(Number(g.share_1r ?? 0) * g.count);
      tiles.push(
        <Tile
          key="target-best"
          title={`Highest high in the ${sessions} sessions after a target fill, minus the exit price, in R. The most a trade could have added; nobody sells at the top.`}
          label="Target exits · best after"
          value={r(g.median_best_r, 1)}
          valueColor={rColor(g.median_best_r)}
          sub={`median of ${g.count} · ${n1} of ${g.count} ran ≥ 1R further`}
        />,
      );
    }
    const subs = [`median of ${g.count}`];
    if (g.reason === 'stop')
      subs.push(`${g.recovered_above_entry} recovered above entry`);
    else if (g.reason !== 'target')
      subs.length = 0;
    tiles.push(
      <Tile
        key={`${g.reason}-close`}
        title={
          g.reason === 'target'
            ? `Close of the ${sessions}th session after a target fill, minus the exit price, in R. More realistic than the best price.`
            : g.reason === 'stop'
              ? `Close of the ${sessions}th session after a stop fill, minus the exit price, in R. High values mean stops were shaken out before the move.`
              : `Close of the ${sessions}th session after a model exit, minus the exit price, in R. Negative means the exit avoided a fall.`
        }
        label={`${name} · ${sessions}-session close`}
        value={r(g.median_close_r, 1)}
        valueColor={rColor(g.median_close_r)}
        sub={subs.length ? subs.join(' · ') : `${g.count} ${g.count === 1 ? 'exit' : 'exits'}`}
      />,
    );
    return tiles;
  });

  const win = data.largest_win;
  const loss = data.largest_loss;
  const top = data.top_share;
  const net = data.net_without_best;
  const kept = data.winners_kept;

  return (
    <>
      {data.trades < data.min_trades && (
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: '10px 14px',
            alignItems: 'center',
            padding: '10px 12px',
            border: '1px solid var(--sa-warning-border)',
            background: 'var(--sa-warning-bg)',
            borderRadius: 'var(--sa-radius-md)',
            fontSize: 'var(--sa-text-md)',
          }}
        >
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              height: 24,
              padding: '0 8px',
              borderRadius: 6,
              ...mono,
              fontSize: 'var(--sa-text-sm)',
              whiteSpace: 'nowrap',
              color: 'var(--sa-warning-text)',
              border: '1px solid var(--sa-warning-border)',
            }}
          >
            {data.trades} of {data.min_trades} trades
          </span>
          <span style={{ color: 'var(--sa-text-2)' }}>
            Too few closed trades to read the tail yet. Values are shown, but one
            trade can swing every number on this card.
          </span>
        </div>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-5)', alignItems: 'flex-start' }}>
        <div style={{ flex: '1 1 420px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>
            Closed trades by result in R (adjusted). Each bar runs from its label
            up to the next
          </div>
          <div
            role="img"
            aria-label={`Distribution of closed trades in R${labelled ? `: ${labelled}` : ': no trades yet'}`}
            style={{
              display: 'flex',
              gap: 4,
              alignItems: 'flex-end',
              height: 192,
              borderBottom: '1px solid var(--sa-border)',
            }}
          >
            {data.distribution.map((b, i) => {
              const label = bucketLabel(b.lower, b.upper);
              const h =
                b.count === 0 ? 2 : Math.max(2, Math.round((b.count / maxCount) * 170));
              const bg =
                b.count === 0
                  ? 'var(--sa-border)'
                  : (b.upper != null && Number(b.upper) <= 0) || (b.lower != null && Number(b.lower) < 0 && b.upper != null && Number(b.upper) <= 0)
                    ? 'var(--sa-negative)'
                    : 'var(--sa-positive)';
              return (
                <div
                  key={i}
                  title={`${b.lower == null ? `< ${label.replace('< ', '')} R` : b.upper == null ? `${label} R` : `${label} R`} bucket: ${b.count} ${b.count === 1 ? 'trade' : 'trades'}`}
                  style={{
                    flex: '1 1 0',
                    minWidth: 22,
                    display: 'flex',
                    flexDirection: 'column',
                    justifyContent: 'flex-end',
                    gap: 4,
                  }}
                >
                  <span
                    style={{
                      ...mono,
                      fontSize: '11px',
                      textAlign: 'center',
                      color: b.count ? 'var(--sa-text-2)' : muted,
                    }}
                  >
                    {b.count}
                  </span>
                  <div
                    style={{
                      height: h,
                      borderRadius: '3px 3px 0 0',
                      background: bg,
                    }}
                  />
                </div>
              );
            })}
          </div>
          <div style={{ display: 'flex', gap: 4 }}>
            {data.distribution.map((b, i) => (
              <span
                key={i}
                style={{
                  flex: '1 1 0',
                  minWidth: 22,
                  ...mono,
                  fontSize: '10px',
                  color: muted,
                  textAlign: 'center',
                  lineHeight: 1.2,
                }}
              >
                {bucketLabel(b.lower, b.upper)}
              </span>
            ))}
          </div>
        </div>

        <div style={{ flex: '1 1 380px', minWidth: 0, display: 'flex', flexWrap: 'wrap', gap: 10 }}>
          {win && (
            <Tile
              title="Best closed trade in R, adjusted. A tail strategy needs some of these to be much larger than the average."
              label="Largest win"
              value={r(win.r)}
              valueColor={positive}
              sub={`${win.symbol} · ${exitLabel(win.exit_reason)} · ${day(win.opened_at)} → ${day(win.closed_at)}`}
            />
          )}
          {loss && (
            <Tile
              title="Worst closed trade in R. Below −1R means the exit filled worse than the stop (slippage or a gap)."
              label="Largest loss"
              value={r(loss.r)}
              valueColor={negative}
              sub={`${loss.symbol} · ${exitLabel(loss.exit_reason)} · ${
                loss.below_stop ? 'filled below the stop' : `${day(loss.opened_at)} → ${day(loss.closed_at)}`
              }`}
            />
          )}
          {top && (
            <Tile
              title={`The best ${topPct}% of closed trades (rounded up) by adjusted P&L, as a share of the summed P&L of all winning trades.`}
              label={`Top ${topPct}% of trades`}
              value={`${Math.round(Number(top.share) * 100)}%`}
              sub={`of gross profit · ${top.trades} of ${top.of} trades`}
            />
          )}
          {net && (
            <Tile
              title="Adjusted net P&L of closed trades with the single best trade removed: how much the result leans on one trade."
              label="Net without the best trade"
              value={money(net.net)}
              valueColor={rColor(net.net)}
              sub={`of ${money(net.total)} · without ${net.symbol}`}
            />
          )}
          {kept && (
            <Tile
              title="Per winning trade: realized R ÷ best R reached while open, averaged."
              label="Winners kept"
              value={`${Math.round(Number(kept.share) * 100)}%`}
              sub={`of their best open gain · ${kept.winners} ${kept.winners === 1 ? 'winner' : 'winners'}`}
            />
          )}
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: '6px 16px' }}>
          <h3 style={{ margin: 0, fontSize: 'var(--sa-text-md)', fontWeight: 600, color: 'var(--sa-text-2)' }}>
            After the exit
          </h3>
          <span style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>
            Movement in the {sessions} sessions after each exit, in R of that
            trade&rsquo;s initial risk
          </span>
        </div>
        {afterTiles.length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>{afterTiles}</div>
        )}
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', minWidth: 620 }}>
            <thead>
              <tr>
                {['Symbol', 'Exit', 'Result', 'Best while open', 'Best after exit', `Close ${sessions} sessions after`].map(
                  (h, i) => (
                    <th
                      key={h}
                      scope="col"
                      style={{
                        textAlign: i < 2 ? 'left' : 'right',
                        padding: 'var(--sa-space-2) 10px',
                        fontSize: 'var(--sa-text-sm)',
                        fontWeight: 500,
                        color: muted,
                        borderBottom: '1px solid var(--sa-border)',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {data.rows.length === 0 ? (
                <tr>
                  <td colSpan={6} style={{ padding: '10px', color: muted, fontSize: 'var(--sa-text-md)' }}>
                    No closed trades yet — the table fills as trades close.
                  </td>
                </tr>
              ) : (
                data.rows.map((trade) => (
                  <Row key={trade.id} trade={trade} />
                ))
              )}
            </tbody>
          </table>
        </div>
        <div style={{ fontSize: 'var(--sa-text-sm)', color: muted, lineHeight: 1.5 }}>
          How to read it: if target exits regularly run 1R or more further, fixed
          targets cut off the winners that pay for the losers, and testing another
          exit style is worth it. If they don&rsquo;t, fixed targets are fine.
          Trades still inside their {sessions}-session window show
          &ldquo;pending&rdquo;.
        </div>
      </div>
    </>
  );
}
