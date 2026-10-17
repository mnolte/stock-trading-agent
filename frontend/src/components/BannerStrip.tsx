import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { useLiveUpdates } from '../api/liveContext';
import {
  getBanners,
  postAcknowledgeEvent,
  postAcknowledgePosition,
  postPauseResume,
} from '../api/state';
import type { BannerItem } from '../types/api';
import { displayTime } from '../format';
import { Banner } from './Banner';
import { Button } from './Button';

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
        if (item.actions.includes('resume_pause')) {
          actions.push({ label: 'Resume', onClick: resumePause });
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
        if (item.actions.includes('acknowledge')) {
          actions.push({ label: 'Acknowledge', onClick: () => acknowledge(item) });
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
    </div>
  );
}
