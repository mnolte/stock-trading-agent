import type { CSSProperties, ReactNode } from 'react';

/**
 * Skeleton shapes from design/screens/Loading.dc.html → Skeleton shapes:
 * the layout of the data, not a generic block. Every bar is `.sa-skel`
 * (aria-hidden); a skeleton never shows a number, a 0 or a dash.
 */

function Bar({
  w,
  h = 12,
  r = 4,
  style,
}: {
  w: string | number;
  h?: number;
  r?: number;
  style?: CSSProperties;
}) {
  return (
    <span
      className="sa-skel"
      aria-hidden="true"
      style={{
        display: 'block',
        height: h,
        width: w,
        maxWidth: '100%',
        borderRadius: r,
        ...style,
      }}
    />
  );
}

const tileStyle: CSSProperties = {
  flex: '1 1 190px',
  padding: 'var(--sa-space-4)',
  background: 'var(--sa-panel)',
  border: '1px solid var(--sa-border)',
  borderRadius: 'var(--sa-radius-lg)',
  display: 'flex',
  flexDirection: 'column',
  gap: 10,
  minWidth: 0,
};

/** Stat tile: label, value and sub-line bars at the filled tile's height. */
export function SkeletonTile({ style }: { style?: CSSProperties }) {
  return (
    <div aria-hidden="true" style={{ ...tileStyle, ...style }}>
      <Bar w="40%" h={10} />
      <Bar w="55%" h={22} r={6} />
      <Bar w="80%" h={10} />
    </div>
  );
}

/** Line bars 12 px; the last one shorter. `lines` defaults to 4. */
export function SkeletonText({ lines = 4 }: { lines?: number }) {
  const widths = ['92%', '86%', '74%', '52%', '64%', '40%'];
  return (
    <div aria-hidden="true" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {Array.from({ length: lines }, (_, i) => (
        <Bar key={i} w={widths[i % widths.length]} />
      ))}
    </div>
  );
}

export interface SkeletonColumn {
  /** Real header text — column headers are never bars. */
  label: ReactNode;
  align?: 'left' | 'right';
}

const CELL_WIDTHS = ['70%', '55%', '85%', '60%', '75%', '45%', '65%', '50%'];

/** Table: real column headers, 4–8 body rows of 44 px with bar widths
 * that vary per row and column. `track` is the shared
 * grid-template-columns for header and rows. */
export function SkeletonTable({
  columns,
  rows = 6,
  track,
}: {
  columns: SkeletonColumn[];
  rows?: number;
  /** grid-template-columns shared by the header and every row. */
  track: string;
}) {
  return (
    <div aria-hidden="true">
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: track,
          gap: 12,
          paddingBottom: 8,
          fontSize: 'var(--sa-text-sm)',
          color: 'var(--sa-text-muted)',
        }}
      >
        {columns.map((c, i) => (
          <span key={i} style={{ textAlign: c.align ?? 'left' }}>
            {c.label}
          </span>
        ))}
      </div>
      {Array.from({ length: rows }, (_, r) => (
        <div
          key={r}
          style={{
            display: 'grid',
            gridTemplateColumns: track,
            gap: 12,
            minHeight: 'var(--sa-control-height)',
            alignItems: 'center',
            borderTop: '1px solid var(--sa-border)',
          }}
        >
          {columns.map((c, ci) => (
            <span key={ci} style={{ display: 'flex', alignItems: 'center', justifyContent: c.align === 'right' ? 'flex-end' : 'flex-start' }}>
              <Bar w={CELL_WIDTHS[(r * columns.length + ci * 3) % CELL_WIDTHS.length]} />
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}

const LIST_BADGE_W = [52, 44, 60, 48, 56, 40];
const LIST_W1 = ['62%', '48%', '70%', '55%', '66%', '44%'];
const LIST_W2 = ['38%', '30%', '42%', '34%', '40%', '28%'];

/** List/feed rows: a badge-sized bar and two lines per row, 44 px each. */
export function SkeletonList({ rows = 4 }: { rows?: number }) {
  return (
    <div aria-hidden="true" style={{ display: 'flex', flexDirection: 'column' }}>
      {Array.from({ length: rows }, (_, r) => (
        <div
          key={r}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--sa-space-3)',
            minHeight: 'var(--sa-control-height)',
            borderBottom: '1px solid var(--sa-border)',
          }}
        >
          <Bar w={LIST_BADGE_W[r % LIST_BADGE_W.length]} h={20} r={5} />
          <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
            <Bar w={LIST_W1[r % LIST_W1.length]} />
            <Bar w={LIST_W2[r % LIST_W2.length]} h={10} />
          </span>
        </div>
      ))}
    </div>
  );
}

/** Chart: real grid lines, one faint plot block, three axis bars —
 * never fake candles or a line. */
export function SkeletonChart({ height = 320 }: { height?: number }) {
  const lines = [0.2, 0.4, 0.6, 0.8];
  return (
    <div aria-hidden="true" style={{ position: 'relative', height }}>
      {lines.map((t) => (
        <span
          key={t}
          style={{
            position: 'absolute',
            left: 0,
            right: 48,
            top: `${t * 100}%`,
            borderTop: '1px solid var(--sa-border)',
          }}
        />
      ))}
      <span
        className="sa-skel"
        style={{
          position: 'absolute',
          left: 0,
          right: 48,
          top: 0,
          bottom: 18,
          borderRadius: 'var(--sa-radius-md)',
          opacity: 0.55,
        }}
      />
      <span
        style={{
          position: 'absolute',
          right: 0,
          top: 0,
          bottom: 18,
          width: 40,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'space-between',
        }}
      >
        {[0, 1, 2].map((i) => (
          <Bar key={i} w={36} h={10} />
        ))}
      </span>
    </div>
  );
}

/** Chip / pill bar at the usual 24 px chip height. */
export function SkeletonChip({ width = 96 }: { width?: number | string }) {
  return (
    <span
      className="sa-skel"
      aria-hidden="true"
      style={{
        display: 'inline-block',
        verticalAlign: 'middle',
        height: 24,
        width,
        borderRadius: 999,
      }}
    />
  );
}

/** Convenience: a row of stat tiles (the dashboard's account summary). */
export function SkeletonTiles({ count = 2 }: { count?: number }) {
  return (
    <div
      aria-hidden="true"
      style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-3)' }}
    >
      {Array.from({ length: count }, (_, i) => (
        <SkeletonTile key={i} />
      ))}
    </div>
  );
}
