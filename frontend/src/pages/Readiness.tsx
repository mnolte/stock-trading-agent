import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { useLiveUpdates } from '../api/liveContext';
import {
  getReadiness,
  postDismissEvent,
  postReadinessManual,
} from '../api/state';
import { Card } from '../components/Card';
import { CardBoundary } from '../components/CardBoundary';
import { Chip } from '../components/Chip';
import { DialogButton, Modal } from '../components/Modal';
import { StatCard } from '../components/StatCard';
import { SkeletonList, SkeletonTiles } from '../components/skeletons';
import { displayDayTime } from '../format';
import {
  BUILDING_UP,
  criterionSub,
  statusText,
  statusVariant,
} from './readinessFormat';
import type {
  ReadinessCriterion,
  ReadinessResponse,
} from '../types/api';

const mono: React.CSSProperties = { fontFamily: 'var(--sa-font-mono)' };
const muted = 'var(--sa-text-muted)';

/** Counts that grow by themselves read "building up" (item 100). */

const GROUP_SUB: Record<string, string> = {
  Evidence: 'From this run, after slippage. Ratios need enough closed trades before they can pass.',
  Model: 'Whether the model’s proposals and confidence can be trusted.',
  Operations: 'Whether the agent runs safely without you watching.',
};

/** "15 Oct" — a stored ISO datetime or date shown without the year. */
function shortDay(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  return d.toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    timeZone: 'Europe/Amsterdam',
  });
}

/** "What moves this forward" — item 100, written by code from the
 * snapshot rows (docs/SPEC.md → Readiness). */
function ForwardCard({ data }: { data: ReadinessResponse | null }) {
  const forward = data?.forward;
  if (!forward) return null;
  const hasAct = forward.act.length > 0;
  const hasTime = forward.time.length > 0;
  const hasManual = forward.manual_open > 0;
  if (!hasAct && !hasTime && !hasManual) return null;

  const sectionLabel: React.CSSProperties = {
    margin: 0,
    fontSize: 'var(--sa-text-sm)',
    fontWeight: 600,
    color: 'var(--sa-text-2)',
    textTransform: 'uppercase',
    letterSpacing: 'var(--sa-label-tracking)',
  };

  return (
    <Card
      label="What moves this forward"
      right={
        <span style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>
          written by code from the criteria below
        </span>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-4)' }}>
        <div>
          <h3 style={sectionLabel}>
            You can act on these now · {forward.act.length}
          </h3>
          {hasAct ? (
            forward.act.map((item) => (
              <div
                key={item.key}
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: '8px 16px',
                  alignItems: 'center',
                  padding: '12px 0',
                  borderBottom: '1px solid var(--sa-border)',
                }}
              >
                <div style={{ flex: '1.4 1 240px', minWidth: 0 }}>
                  <div style={{ fontSize: 'var(--sa-text-md)' }}>{item.name}</div>
                  <div style={{ fontSize: 'var(--sa-text-sm)', color: muted, marginTop: 2 }}>
                    {item.value_text}
                  </div>
                </div>
                <div style={{ flex: '2 1 300px', minWidth: 0, fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.5 }}>
                  {item.sentence}
                </div>
                <Link
                  to={item.href}
                  style={{
                    flex: '0 0 auto',
                    height: 32,
                    boxSizing: 'border-box',
                    padding: '0 12px',
                    display: 'inline-flex',
                    alignItems: 'center',
                    borderRadius: 'var(--sa-radius-sm)',
                    border: '1px solid var(--sa-border-strong)',
                    background: 'var(--sa-raised)',
                    color: 'var(--sa-text)',
                    fontSize: 'var(--sa-text-md)',
                    fontWeight: 600,
                    textDecoration: 'none',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {item.button}
                </Link>
              </div>
            ))
          ) : (
            <div style={{ padding: '12px 0', fontSize: 'var(--sa-text-md)', color: muted }}>
              Nothing to act on now.
            </div>
          )}
        </div>

        {hasTime && (
          <div>
            <h3 style={sectionLabel}>
              Only time fixes these · {forward.time_count}
            </h3>
            <div style={{ padding: '10px 0', display: 'flex', flexDirection: 'column', gap: 6 }}>
              {forward.time
                .filter((t) => t.kind === 'count')
                .map((t) => (
                  <div key={t.key} style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>
                    <span style={{ fontWeight: 600 }}>{t.name}</span>
                    <span style={{ color: muted }}> {t.text}</span>
                  </div>
                ))}
              {forward.time.some((t) => t.kind === 'needs') && (
                <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.5 }}>
                  <span style={{ fontWeight: 600 }}>Needs data</span>
                  <span style={{ color: muted }}>
                    {' '}
                    {forward.time
                      .filter((t) => t.kind === 'needs')
                      .map((t) => t.text)
                      .join('; ')}
                    .
                  </span>
                </div>
              )}
            </div>
          </div>
        )}

        {hasManual && (
          <div>
            <h3 style={sectionLabel}>
              Yours to do whenever you choose · {forward.manual_open}
            </h3>
            <div style={{ padding: '10px 0', display: 'flex', flexWrap: 'wrap', gap: '8px 16px', alignItems: 'center', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>
              <span>
                The {forward.manual_open} manual{' '}
                {forward.manual_open === 1 ? 'item' : 'items'} at the end
                of this page.
              </span>
              <a href="#manual-items" style={{ color: 'var(--sa-accent)' }}>
                Go to Manual items
              </a>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

function CriterionRow({ criterion }: { criterion: ReadinessCriterion }) {
  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: '8px 16px',
        alignItems: 'center',
        padding: '12px 0',
        borderBottom: '1px solid var(--sa-border)',
      }}
    >
      <div style={{ flex: '1.6 1 260px', minWidth: 0 }}>
        <div
          title={criterion.hint}
          style={{
            fontSize: 'var(--sa-text-md)',
            textDecoration: 'underline dotted',
            textUnderlineOffset: '3px',
            textDecorationColor: 'var(--sa-border-strong)',
            cursor: 'help',
            display: 'inline',
          }}
        >
          {criterion.label}
        </div>
        {criterionSub(criterion) && (
          <div style={{ fontSize: 'var(--sa-text-sm)', color: muted, marginTop: '4px' }}>
            {criterionSub(criterion)}
          </div>
        )}
      </div>
      <div style={{ flex: '1 1 200px', minWidth: 0 }}>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            gap: '8px',
            fontSize: 'var(--sa-text-sm)',
          }}
        >
          <span style={mono}>{criterion.value}</span>
          <span style={{ color: muted }}>target {criterion.target}</span>
        </div>
        {criterion.progress != null && (
          <div
            style={{
              height: '6px',
              borderRadius: '3px',
              background: 'var(--sa-border)',
              marginTop: '6px',
            }}
          >
            <div
              className="sa-bar-fill"
              style={{
                width: `${Math.min(Math.max(criterion.progress, 0), 1) * 100}%`,
                height: '6px',
                borderRadius: '3px',
                background: 'var(--sa-accent-2, #9FB4CC)',
              }}
            />
          </div>
        )}
      </div>
      <div
        style={{
          flex: '0 1 140px',
          marginLeft: 'auto',
          display: 'flex',
          justifyContent: 'flex-end',
        }}
      >
        <Chip variant={statusVariant(criterion)}>
          {statusText(criterion)}
        </Chip>
      </div>
    </div>
  );
}

export default function Readiness() {
  const [data, setData] = useState<ReadinessResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [manualDialog, setManualDialog] = useState<{
    item: string;
    label: string;
    done: boolean;
  } | null>(null);
  const [dismissDialog, setDismissDialog] = useState<number | null>(null);
  const [note, setNote] = useState('');
  const [noteError, setNoteError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    getReadiness().then(setData).catch((e: Error) => setError(e.message));
  }, []);
  useLiveUpdates(['readiness'], refresh);
  useEffect(refresh, [refresh]);

  const openNote = () => {
    setNote('');
    setNoteError(null);
  };

  const submitManual = () => {
    if (!manualDialog) return;
    if (!note.trim()) {
      setNoteError('A short note is required.');
      return;
    }
    postReadinessManual(manualDialog.item, manualDialog.done, note.trim())
      .then(() => {
        setManualDialog(null);
        refresh();
      })
      .catch((e: Error) => setNoteError(e.message));
  };

  const submitDismiss = () => {
    if (dismissDialog == null) return;
    if (!note.trim()) {
      setNoteError('A short note is required.');
      return;
    }
    postDismissEvent(dismissDialog, note.trim())
      .then(() => {
        setDismissDialog(null);
        refresh();
      })
      .catch((e: Error) => setNoteError(e.message));
  };

  const criteria = data?.snapshot?.criteria ?? [];
  const groups = ['Evidence', 'Model', 'Operations'];
  const ready =
    data != null &&
    criteria.length > 0 &&
    data.summary.pass === data.summary.total &&
    data.summary.manual_done === data.summary.manual_total;
  const buildingUp = criteria.filter(
    (c) => c.status === 'not_yet' && BUILDING_UP.has(c.key),
  ).length;
  const belowTarget = (data?.summary.not_yet ?? 0) - buildingUp;

  if (!data && !error) {
    return (
      <div
        role="status"
        aria-label="Loading readiness"
        style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}
      >
        <span className="sa-sr">Loading readiness</span>
        <div className="sa-loading">
          <SkeletonTiles count={3} />
          <SkeletonList rows={8} />
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}>
      {error && (
        <div style={{ color: 'var(--sa-negative-text)', fontSize: 'var(--sa-text-sm)' }}>
          {error}
        </div>
      )}

      <section
        aria-label="Readiness verdict"
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: '12px 20px',
          padding: '16px 20px',
          border: `1px solid ${ready ? 'var(--sa-positive-border)' : 'var(--sa-border)'}`,
          background: ready ? 'var(--sa-positive-bg)' : 'var(--sa-panel)',
          borderRadius: 'var(--sa-radius-lg)',
        }}
      >
        <CardBoundary name="Readiness verdict">
        <span style={{ color: ready ? 'var(--sa-positive)' : 'var(--sa-text-2)' }}>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />
            <polyline points="9 12 11 14 15 10" />
          </svg>
        </span>
        <div style={{ flex: '1 1 420px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <span
            style={{
              fontSize: 'var(--sa-text-lg)',
              fontWeight: 600,
              color: ready ? 'var(--sa-positive)' : 'var(--sa-text)',
            }}
          >
            {ready
              ? 'Every criterion passes and every manual item is done'
              : `Not ready for live trading · ${data?.summary.pass ?? 0} of ${data?.summary.total ?? 0} criteria pass`}
          </span>
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-2)', lineHeight: 1.5 }}>
            {!ready &&
            data?.summary.sessions != null &&
            data.summary.sessions < data.summary.min_sessions
              ? `That is expected ${data.summary.sessions} sessions into a run. `
              : ''}
            Live trading isn’t built: this page shows when the paper evidence is
            strong enough to start the live spec. Every criterion must pass and
            every manual item must be done; nothing unlocks automatically.
          </span>
        </div>
        <span style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>
          {data?.snapshot
            ? `Checked after the close · ${displayDayTime(data.snapshot.created_at)}`
            : 'No snapshot yet — written after each session’s close'}
          {data?.run ? ` · run since ${shortDay(data.run.started_at)}` : ''}
        </span>
        </CardBoundary>
      </section>

      <section aria-label="Readiness summary" style={{ display: 'flex', flexWrap: 'wrap', gap: '12px' }}>
        <CardBoundary name="Readiness summary">
        <StatCard label="Pass" value={String(data?.summary.pass ?? 0)} valueColor="var(--sa-positive)" sub={`of ${data?.summary.total ?? 15} criteria`} />
        <StatCard label="Below target" value={String(belowTarget)} valueColor="var(--sa-negative-text)" sub="need a change" />
        <StatCard label="Building up" value={String(buildingUp)} valueColor="var(--sa-info)" sub="need more sessions" />
        <StatCard label="Needs data" value={String(data?.summary.waiting ?? 0)} sub="samples too small" />
        <StatCard label="Manual items" value={`${data?.summary.manual_done ?? 0} of ${data?.summary.manual_total ?? 5}`} sub="done" />
        <StatCard
          label="Earliest possible"
          value={data?.summary.earliest?.text ?? '—'}
          sub={data?.summary.earliest ? `limited by ${data.summary.earliest.label.toLowerCase()}` : 'no open counts'}
        />
        </CardBoundary>
      </section>

      <ForwardCard data={data} />

      {groups.map((group) => (
        <Card
          key={group}
          label={group}
          right={
            group === 'Evidence' ? (
              <Link to="/performance" style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>Performance</Link>
            ) : group === 'Model' ? (
              <Link to="/strategies" style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>Strategies</Link>
            ) : group === 'Operations' ? (
              <Link to="/logs" style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>Logs</Link>
            ) : undefined
          }
        >
          <div style={{ fontSize: 'var(--sa-text-sm)', color: muted, marginTop: '-6px' }}>
            {GROUP_SUB[group]}
          </div>
          {criteria
            .filter((c) => c.group === group)
            .map((c) => (
              <CriterionRow key={c.key} criterion={c} />
            ))}
          {criteria.length === 0 && (
            <div style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>
              Waiting for the first snapshot.
            </div>
          )}
        </Card>
      ))}

      {(data?.errors.length ?? 0) > 0 && (
        <Card label="Unresolved errors" right={<span style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>dismiss with a note</span>}>
          {data!.errors.map((e) => (
            <div
              key={e.id}
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: '8px 16px',
                alignItems: 'center',
                padding: '12px 0',
                borderBottom: '1px solid var(--sa-border)',
              }}
            >
              <div style={{ flex: '1 1 300px', minWidth: 0 }}>
                <div style={{ fontSize: 'var(--sa-text-md)' }}>{e.message}</div>
                <div style={{ fontSize: 'var(--sa-text-sm)', color: muted, marginTop: '4px' }}>
                  {displayDayTime(e.time)} · affects {e.affects} · auto-resolves {displayDayTime(e.resolves_at)} if quiet
                </div>
              </div>
              <button
                type="button"
                onClick={() => {
                  openNote();
                  setDismissDialog(e.id);
                }}
                style={{
                  height: '32px',
                  padding: '0 12px',
                  borderRadius: 'var(--sa-radius-md)',
                  border: '1px solid var(--sa-border-strong)',
                  background: 'var(--sa-panel-2)',
                  color: 'var(--sa-text)',
                  fontSize: 'var(--sa-text-sm)',
                  cursor: 'pointer',
                }}
              >
                Dismiss…
              </button>
            </div>
          ))}
        </Card>
      )}

      <div id="manual-items" style={{ scrollMarginTop: 24 }}>
      <Card
        label="Manual items"
        right={
          <span style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>
            {data?.summary.manual_done ?? 0} of {data?.summary.manual_total ?? 5} done
          </span>
        }
      >
        {(data?.manual ?? []).map((item) => (
          <div
            key={item.item}
            style={{
              display: 'flex',
              gap: '12px',
              alignItems: 'center',
              padding: '12px 0',
              borderBottom: '1px solid var(--sa-border)',
            }}
          >
            <button
              type="button"
              role="checkbox"
              aria-checked={item.done}
              aria-label={item.label}
              onClick={() => {
                openNote();
                setManualDialog({ item: item.item, label: item.label, done: !item.done });
              }}
              style={{
                width: '22px',
                height: '22px',
                flex: '0 0 22px',
                borderRadius: '5px',
                border: `1px solid ${item.done ? 'var(--sa-positive-border)' : 'var(--sa-border-strong)'}`,
                background: item.done ? 'var(--sa-positive-bg)' : 'var(--sa-bg)',
                color: 'var(--sa-positive)',
                padding: 0,
                cursor: 'pointer',
                fontSize: '14px',
                lineHeight: '20px',
              }}
            >
              {item.done ? '✓' : ''}
            </button>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 'var(--sa-text-md)' }}>{item.label}</div>
              <div style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>{item.detail}</div>
              {item.done && item.note && (
                <div style={{ fontSize: 'var(--sa-text-sm)', color: muted, marginTop: '2px' }}>
                  {shortDay(item.changed_at)} — {item.note}
                </div>
              )}
            </div>
            <Chip variant={item.done ? 'positive' : 'neutral'}>
              {item.done ? 'done' : 'to do'}
            </Chip>
          </div>
        ))}
        <div style={{ fontSize: 'var(--sa-text-sm)', color: muted, paddingTop: '10px' }}>
          Ticking an item asks for a short note; it is stored with the date and shown in History.
        </div>
      </Card>
      </div>

      <Card
        label="History"
        right={<span style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>every status and manual change</span>}
      >
        <div style={{ overflowX: 'auto', minWidth: 0 }}>
          <table style={{ width: '100%', minWidth: '420px', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th scope="col" style={{ textAlign: 'left', padding: '8px 10px', fontSize: 'var(--sa-text-sm)', fontWeight: 500, color: muted, borderBottom: '1px solid var(--sa-border)', whiteSpace: 'nowrap' }}>Date</th>
                <th scope="col" style={{ textAlign: 'left', padding: '8px 10px', fontSize: 'var(--sa-text-sm)', fontWeight: 500, color: muted, borderBottom: '1px solid var(--sa-border)', whiteSpace: 'nowrap' }}>Change</th>
              </tr>
            </thead>
            <tbody>
              {(data?.history ?? []).map((row, i) => (
                <tr key={i}>
                  <td style={{ ...mono, textAlign: 'left', padding: '10px', borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-sm)', verticalAlign: 'top' }}>
                    {shortDay(row.at)}
                  </td>
                  <td style={{ textAlign: 'left', padding: '10px', borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-sm)', verticalAlign: 'top' }}>
                    {row.change}
                  </td>
                </tr>
              ))}
              {(data?.history ?? []).length === 0 && (
                <tr>
                  <td colSpan={2} style={{ padding: '10px', fontSize: 'var(--sa-text-sm)', color: muted }}>
                    Nothing yet — changes appear after the first snapshots.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div style={{ fontSize: 'var(--sa-text-sm)', color: muted }}>
          Thresholds are in Settings → Readiness; changing one is a settings change with a reason.
        </div>
      </Card>

      <Modal
        open={manualDialog != null}
        title={manualDialog ? `${manualDialog.done ? 'Tick' : 'Untick'} “${manualDialog.label}”` : ''}
        onClose={() => setManualDialog(null)}
        footer={
          <>
            <DialogButton variant="cancel" onClick={() => setManualDialog(null)}>Cancel</DialogButton>
            <DialogButton variant="accent" onClick={submitManual}>Save</DialogButton>
          </>
        }
      >
        <p style={{ margin: 0, lineHeight: 1.55, color: 'var(--sa-text-2)', fontSize: 'var(--sa-text-md)' }}>
          A short note is stored with the date and shown in History.
        </p>
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          aria-label="Note"
          rows={3}
          style={{
            width: '100%',
            boxSizing: 'border-box',
            padding: '10px',
            borderRadius: 'var(--sa-radius-md)',
            border: '1px solid var(--sa-border-strong)',
            background: 'var(--sa-bg)',
            color: 'var(--sa-text)',
            fontSize: 'var(--sa-text-md)',
            resize: 'vertical',
          }}
        />
        {noteError && <div style={{ color: 'var(--sa-negative-text)', fontSize: 'var(--sa-text-sm)' }}>{noteError}</div>}
      </Modal>

      <Modal
        open={dismissDialog != null}
        title="Dismiss error"
        onClose={() => setDismissDialog(null)}
        footer={
          <>
            <DialogButton variant="cancel" onClick={() => setDismissDialog(null)}>Cancel</DialogButton>
            <DialogButton variant="accent" onClick={submitDismiss}>Dismiss</DialogButton>
          </>
        }
      >
        <p style={{ margin: 0, lineHeight: 1.55, color: 'var(--sa-text-2)', fontSize: 'var(--sa-text-md)' }}>
          Dismissing resolves the error for Readiness. Write a short note — it stays in the event history.
        </p>
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          aria-label="Note"
          rows={3}
          style={{
            width: '100%',
            boxSizing: 'border-box',
            padding: '10px',
            borderRadius: 'var(--sa-radius-md)',
            border: '1px solid var(--sa-border-strong)',
            background: 'var(--sa-bg)',
            color: 'var(--sa-text)',
            fontSize: 'var(--sa-text-md)',
            resize: 'vertical',
          }}
        />
        {noteError && <div style={{ color: 'var(--sa-negative-text)', fontSize: 'var(--sa-text-sm)' }}>{noteError}</div>}
      </Modal>
    </div>
  );
}
