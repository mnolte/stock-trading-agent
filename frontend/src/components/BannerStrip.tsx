import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { useLiveUpdates } from '../api/liveContext';
import {
  getBanners,
  postAcknowledgeEvent,
  postAcknowledgePosition,
  postDismissEvent,
  postDismissUncleanRun,
  postPauseResume,
} from '../api/state';
import type { BannerItem } from '../types/api';
import { displayTime } from '../format';
import { Banner } from './Banner';
import { Button } from './Button';
import { DialogButton, Modal } from './Modal';

const bannerVariant = (level: string): 'negative' | 'warning' | 'info' =>
  level === 'critical' ? 'negative' : level === 'warning' ? 'warning' : 'info';

function bannerText(item: BannerItem): string {
  switch (item.kind) {
    case 'kill_switch':
      return item.automatic
        ? `Trading halted — ${item.reason ?? 'automatic'}`
        : 'Kill switch is on — no new entries. Open positions stay protected at Alpaca.';
    case 'reconciliation':
      return `Reconciliation mismatch — Alpaca shows something different. ${item.message}`;
    default:
      return item.message;
  }
}

/** Renders the banner stack from /api/banners above the page content. */
export function BannerStrip({
  onResumeRequest,
  onChanged,
  refreshKey = 0,
}: {
  onResumeRequest: (item: BannerItem) => void;
  onChanged?: () => void;
  refreshKey?: number;
}) {
  const [items, setItems] = useState<BannerItem[]>([]);
  const [pending, setPending] = useState(false);
  const [dismissItem, setDismissItem] = useState<BannerItem | null>(null);
  const [dismissNote, setDismissNote] = useState('');
  const [dismissError, setDismissError] = useState<string | null>(null);
  const navigate = useNavigate();

  const refresh = useCallback(() => {
    getBanners()
      .then((data) => setItems(data.items))
      .catch(console.error);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh, refreshKey]);
  useLiveUpdates(['banners'], refresh);

  // Pause resume is one click, no reason (docs/SPEC.md, Paused at startup).
  const resumePause = () => {
    setPending(true);
    postPauseResume()
      .then(() => {
        refresh();
        onChanged?.();
      })
      .catch(console.error)
      .finally(() => setPending(false));
  };

  const acknowledge = (item: BannerItem) => {
    setPending(true);
    const call =
      item.position_id != null
        ? postAcknowledgePosition(item.position_id)
        : item.id != null
          ? postAcknowledgeEvent(item.id)
          : Promise.resolve();
    call
      .then(() => {
        refresh();
        onChanged?.();
      })
      .catch(console.error)
      .finally(() => setPending(false));
  };

  // The unclean-run banner has no event row: one click hides it for the
  // rest of this run (design: States).
  const dismissUnclean = () => {
    setPending(true);
    postDismissUncleanRun()
      .then(() => {
        refresh();
        onChanged?.();
      })
      .catch(console.error)
      .finally(() => setPending(false));
  };

  // Errors linked to a decision, order or fill resolve by dismissal with a
  // note, or by themselves after the quiet period (docs/SPEC.md, Readiness).
  const dismiss = () => {
    if (dismissItem?.id == null) return;
    if (!dismissNote.trim()) {
      setDismissError('A short note is required.');
      return;
    }
    setPending(true);
    postDismissEvent(dismissItem.id, dismissNote.trim())
      .then(() => {
        setDismissItem(null);
        refresh();
        onChanged?.();
      })
      .catch((e: Error) => setDismissError(e.message))
      .finally(() => setPending(false));
  };

  if (items.length === 0) return null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-2)', marginBottom: 'var(--sa-space-4)' }}>
      {items.map((item, index) => {
        const actions: { label: string; onClick: () => void }[] = [];
        if (item.actions.includes('resume')) {
          // Kill-switch resume opens the typed-reason dialog ("Resume…");
          // pause resume is a plain one-click "Resume" (design: States).
          actions.push({ label: 'Resume…', onClick: () => onResumeRequest(item) });
        }
        if (item.actions.includes('resume_pause') && !item.resume_blocked) {
          actions.push({ label: 'Resume', onClick: resumePause });
        }
        if (item.actions.includes('open_settings')) {
          actions.push({ label: 'Settings', onClick: () => navigate('/settings') });
        }
        if (item.actions.includes('review') || item.actions.includes('open_orders')) {
          actions.push({
            label: item.actions.includes('review') ? 'Review' : 'Open orders',
            onClick: () => navigate('/positions'),
          });
        }
        if (item.actions.includes('connections')) {
          actions.push({ label: 'Connections', onClick: () => navigate('/settings') });
        }
        if (item.actions.includes('open_logs')) {
          actions.push({ label: 'Open logs', onClick: () => navigate('/logs') });
        }
        if (item.actions.includes('acknowledge')) {
          actions.push({ label: 'Acknowledge', onClick: () => acknowledge(item) });
        }
        if (item.actions.includes('dismiss')) {
          if (item.id != null) {
            actions.push({
              label: 'Dismiss…',
              onClick: () => {
                setDismissNote('');
                setDismissError(null);
                setDismissItem(item);
              },
            });
          } else if (item.kind === 'unclean_run') {
            actions.push({ label: 'Dismiss', onClick: dismissUnclean });
          }
        }
        return (
          <Banner
            key={item.id ?? `${item.kind}-${index}`}
            variant={bannerVariant(item.level)}
            actions={
              actions.length > 0 ? (
                <>
                  {actions.map((a) => (
                    <Button
                      key={a.label}
                      onClick={a.onClick}
                      disabled={pending}
                      style={{ height: '30px', padding: '0 var(--sa-space-3)', fontSize: 'var(--sa-text-sm)' }}
                    >
                      {a.label}
                    </Button>
                  ))}
                </>
              ) : undefined
            }
          >
            {bannerText(item)}
            {item.time && (
              <span style={{ opacity: 0.8 }}> · {displayTime(item.time)}</span>
            )}
          </Banner>
        );
      })}
      <Modal
        open={dismissItem != null}
        title="Dismiss error"
        onClose={() => setDismissItem(null)}
        footer={
          <>
            <DialogButton variant="cancel" onClick={() => setDismissItem(null)}>Cancel</DialogButton>
            <DialogButton variant="accent" onClick={dismiss} disabled={pending}>Dismiss</DialogButton>
          </>
        }
      >
        <p style={{ margin: 0, lineHeight: 1.55, color: 'var(--sa-text-2)', fontSize: 'var(--sa-text-md)' }}>
          {dismissItem != null && bannerText(dismissItem)}
        </p>
        <p style={{ margin: 0, lineHeight: 1.55, color: 'var(--sa-text-2)', fontSize: 'var(--sa-text-md)' }}>
          Dismissing resolves the error for Readiness. Write a short note — it stays in the event history.
        </p>
        <textarea
          value={dismissNote}
          onChange={(e) => setDismissNote(e.target.value)}
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
        {dismissError && (
          <div style={{ color: 'var(--sa-negative-text)', fontSize: 'var(--sa-text-sm)' }}>{dismissError}</div>
        )}
      </Modal>
    </div>
  );
}
