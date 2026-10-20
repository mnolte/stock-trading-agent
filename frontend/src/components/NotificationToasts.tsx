import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getNotifications } from '../api/state';
import { useLiveUpdates } from '../api/liveContext';
import type { NotificationItem } from '../types/api';

// Poll-free in-app toasts: the websocket 'notifications' topic wakes this up,
// then the REST list hands over the new rows (docs/SPEC.md, Notifications).
// A toast shows for every notification; a browser Notification is only raised
// when the backend routed the event to the 'browser' channel and permission
// was granted on the Settings → Notifications card.
const MAX_TOASTS = 4;
const TOAST_MS = 10_000;

const SEVERITY_STYLE: Record<string, { border: string; title: string }> = {
  critical: { border: 'var(--sa-negative-border)', title: 'var(--sa-negative-text)' },
  warning: { border: 'var(--sa-accent-border)', title: 'var(--sa-accent)' },
  info: { border: 'var(--sa-border)', title: 'var(--sa-text)' },
};

function toastSubject(item: NotificationItem): string {
  return item.subject.replace(/^\[Stock Agent[^\]]*\]\s*/, '');
}

function toastBody(item: NotificationItem): string {
  return (item.body ?? '').split('\n')[0];
}

function raiseBrowserNotification(item: NotificationItem) {
  if (item.channel_status.browser !== 'sent') return;
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    new Notification(item.subject, {
      body: toastBody(item),
      tag: `sa-${item.id}`,
    });
  } catch {
    // The API exists but the constructor threw (e.g. denied at OS level).
  }
}

export default function NotificationToasts() {
  const [toasts, setToasts] = useState<NotificationItem[]>([]);
  const lastId = useRef(0);
  const seeded = useRef(false);
  const navigate = useNavigate();

  const fetchNew = useCallback(async () => {
    try {
      const res = await getNotifications(lastId.current, 20);
      if (!seeded.current) {
        // First load seeds the cursor only — old rows are not toasted.
        lastId.current = res.latest_id;
        seeded.current = true;
        return;
      }
      for (const item of [...res.items].reverse()) {
        lastId.current = Math.max(lastId.current, item.id);
        raiseBrowserNotification(item);
        setToasts((current) => [...current.slice(-(MAX_TOASTS - 1)), item]);
      }
    } catch {
      // Backend briefly unreachable; the next topic update retries.
    }
  }, []);

  useEffect(() => {
    void fetchNew();
  }, [fetchNew]);

  useLiveUpdates(['notifications'], () => {
    void fetchNew();
  });

  // Oldest toast leaves first, TOAST_MS after the stack last changed.
  useEffect(() => {
    if (toasts.length === 0) return;
    const timer = setTimeout(() => setToasts((current) => current.slice(1)), TOAST_MS);
    return () => clearTimeout(timer);
  }, [toasts]);

  if (toasts.length === 0) return null;

  return (
    <div
      role="region"
      aria-label="Notifications"
      style={{
        position: 'fixed',
        top: 64,
        right: 16,
        zIndex: 60,
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        width: 'min(360px, calc(100vw - 32px))',
      }}
    >
      {toasts.map((item) => {
        const style = SEVERITY_STYLE[item.severity] ?? SEVERITY_STYLE.info;
        return (
          <div
            key={item.id}
            role={item.severity === 'critical' ? 'alert' : 'status'}
            onClick={() => {
              if (item.link) {
                setToasts((current) => current.filter((t) => t.id !== item.id));
                navigate(item.link);
              }
            }}
            style={{
              display: 'flex',
              gap: 10,
              alignItems: 'flex-start',
              padding: '10px 12px',
              background: 'var(--sa-chrome)',
              border: `1px solid ${style.border}`,
              borderRadius: 'var(--sa-radius-md)',
              boxShadow: '0 8px 24px rgba(0,0,0,0.45)',
              cursor: item.link ? 'pointer' : 'default',
            }}
          >
            <span style={{ flex: '1 1 auto', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
              <span style={{ fontSize: 'var(--sa-text-md)', fontWeight: 600, color: style.title }}>
                {toastSubject(item)}
              </span>
              {item.body && (
                <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-2)', lineHeight: 1.4 }}>
                  {toastBody(item)}
                </span>
              )}
            </span>
            <button
              type="button"
              aria-label="Dismiss notification"
              onClick={(e) => {
                e.stopPropagation();
                setToasts((current) => current.filter((t) => t.id !== item.id));
              }}
              style={{
                flex: '0 0 auto',
                width: 24,
                height: 24,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                border: 0,
                borderRadius: 'var(--sa-radius-sm)',
                background: 'transparent',
                color: 'var(--sa-text-muted)',
                cursor: 'pointer',
              }}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <line x1="6" y1="6" x2="18" y2="18" />
                <line x1="18" y1="6" x2="6" y2="18" />
              </svg>
            </button>
          </div>
        );
      })}
    </div>
  );
}
