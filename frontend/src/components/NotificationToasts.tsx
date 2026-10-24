import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getNotifications } from '../api/state';
import { useLiveUpdates } from '../api/liveContext';
import type { NotificationItem } from '../types/api';

// Poll-free in-app toasts: the websocket 'notifications' topic wakes this up,
// then the REST list hands over the new rows (docs/SPEC.md, Notifications).
// One toast per notification event, whatever the channels; they stack below
// the header, newest on top, at most 3 — older ones collapse into "n more".
// Critical toasts stay until dismissed (role=alert); info and warning close
// after 10 s, and hovering pauses the timer
// (design/screens/States.dc.html, Toasts).
const MAX_VISIBLE = 3;
const TOAST_MS = 10_000;

const SEVERITY_STYLE: Record<
  string,
  { bg: string; border: string; title: string; button: string }
> = {
  critical: {
    bg: 'var(--sa-negative-bg)',
    border: 'var(--sa-negative-border)',
    title: 'var(--sa-negative-soft)',
    button: 'var(--sa-negative-soft)',
  },
  warning: {
    bg: 'var(--sa-raised)',
    border: 'var(--sa-warning-border)',
    title: 'var(--sa-warning-text)',
    button: 'var(--sa-warning-text)',
  },
  info: {
    bg: 'var(--sa-raised)',
    border: 'var(--sa-border-strong)',
    title: 'var(--sa-text)',
    button: 'var(--sa-text-2)',
  },
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

function ToastButton({
  children,
  color,
  onClick,
}: {
  children: string;
  color: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        height: '36px',
        padding: '0 var(--sa-space-3)',
        borderRadius: 'var(--sa-radius-md)',
        border: '1px solid currentColor',
        background: 'transparent',
        color,
        fontSize: 'var(--sa-text-md)',
        fontWeight: 500,
        cursor: 'pointer',
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </button>
  );
}

function ToastCard({
  item,
  onOpen,
  onDismiss,
}: {
  item: NotificationItem;
  onOpen: (item: NotificationItem) => void;
  onDismiss: (id: number) => void;
}) {
  const critical = item.severity === 'critical';
  const [paused, setPaused] = useState(false);
  const remaining = useRef(TOAST_MS);
  const startedAt = useRef(0);

  // Info/warning close 10 s after they appear; hovering pauses the timer.
  // Critical toasts never auto-close.
  useEffect(() => {
    if (critical || paused) return;
    startedAt.current = Date.now();
    const timer = setTimeout(() => onDismiss(item.id), remaining.current);
    return () => {
      clearTimeout(timer);
      remaining.current -= Date.now() - startedAt.current;
    };
  }, [critical, paused, item.id, onDismiss]);

  const style = SEVERITY_STYLE[item.severity] ?? SEVERITY_STYLE.info;
  return (
    <div
      role={critical ? 'alert' : 'status'}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
      style={{
        padding: 'var(--sa-space-3) var(--sa-space-4)',
        borderRadius: 'var(--sa-radius-lg)',
        background: style.bg,
        border: `1px solid ${style.border}`,
        boxShadow: '0 12px 24px rgba(0,0,0,0.4)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sa-space-2)',
      }}
    >
      <div style={{ fontWeight: 600, color: style.title }}>
        {toastSubject(item)}
      </div>
      {item.body && (
        <div
          style={{
            fontSize: 'var(--sa-text-md)',
            color: 'var(--sa-text-2)',
            lineHeight: 1.45,
          }}
        >
          {toastBody(item)}
        </div>
      )}
      <div style={{ display: 'flex', gap: 'var(--sa-space-2)', alignItems: 'center' }}>
        {item.link && (
          <ToastButton color={style.button} onClick={() => onOpen(item)}>
            Open
          </ToastButton>
        )}
        <ToastButton color={style.button} onClick={() => onDismiss(item.id)}>
          Dismiss
        </ToastButton>
        {!critical && (
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
            closes in 10 s
          </span>
        )}
      </div>
    </div>
  );
}

export default function NotificationToasts() {
  // Newest first — the stack renders top-down.
  const [toasts, setToasts] = useState<NotificationItem[]>([]);
  const lastId = useRef(0);
  const seeded = useRef(false);
  const seen = useRef(new Set<number>());
  const navigate = useNavigate();

  const dismiss = useCallback(
    (id: number) =>
      setToasts((current) => current.filter((t) => t.id !== id)),
    [],
  );

  const open = useCallback(
    (item: NotificationItem) => {
      dismiss(item.id);
      if (item.link) navigate(item.link);
    },
    [dismiss, navigate],
  );

  const fetchNew = useCallback(async () => {
    try {
      const res = await getNotifications(lastId.current, 20);
      if (!seeded.current) {
        // First load seeds the cursor only — old rows are not toasted.
        for (const item of res.items) seen.current.add(item.id);
        lastId.current = res.latest_id;
        seeded.current = true;
        return;
      }
      // Oldest → newest so the newest ends up first in the stack.
      for (const item of [...res.items].reverse()) {
        lastId.current = Math.max(lastId.current, item.id);
        // One toast per notification event, whatever the channels or the
        // way it arrived (poll, websocket wake).
        if (seen.current.has(item.id)) continue;
        seen.current.add(item.id);
        raiseBrowserNotification(item);
        setToasts((current) => [item, ...current]);
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

  if (toasts.length === 0) return null;
  const visible = toasts.slice(0, MAX_VISIBLE);
  const hidden = toasts.length - visible.length;

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
        width: 'min(380px, calc(100vw - 32px))',
      }}
    >
      {visible.map((item) => (
        <ToastCard key={item.id} item={item} onOpen={open} onDismiss={dismiss} />
      ))}
      {hidden > 0 && (
        <div
          style={{
            alignSelf: 'flex-end',
            padding: '2px var(--sa-space-3)',
            borderRadius: 'var(--sa-radius-sm)',
            background: 'var(--sa-raised)',
            border: '1px solid var(--sa-border)',
            fontSize: 'var(--sa-text-sm)',
            color: 'var(--sa-text-muted)',
          }}
        >
          {hidden} more
        </div>
      )}
    </div>
  );
}
