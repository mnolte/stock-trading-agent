import { useCallback, useEffect, useRef, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';

import { useLiveStatus, useLiveUpdates } from '../api/liveContext';
import { useApiResource } from '../api/useResource';
import { ApiError } from '../api/client';
import {
  KILL_BANNER_MS,
  KILL_RESEND_MS,
  TOAST_AUTO_CLOSE_MS,
} from '../api/timing';
import { displayTime } from '../format';
import {
  getAttention,
  getHeaderState,
  getPositions,
  postKillSwitch,
  postKillSwitchResume,
  postPause,
  postPauseResume,
} from '../api/state';
import { useMediaQuery } from '../hooks/useMediaQuery';
import type { HeaderState, PositionsResponse } from '../types/api';
import { Banner } from './Banner';
import { BannerStrip } from './BannerStrip';
import { documentTitle, pageTitle } from './documentTitle';
import { Header } from './Header';
import { FlattenConfirmDialog, KillSwitchDialog, ResumeDialog } from './KillSwitchDialogs';
import NotificationToasts from './NotificationToasts';
import { AskDrawer } from '../ask/AskDrawer';
import { useAsk } from '../ask/AskContext';
import { PhoneBar } from './PhoneBar';
import { Sidebar } from './Sidebar';
import { ConfirmYouDialog } from './ConfirmYouDialog';
import { useAuthStatus } from '../api/auth';
import { registerConfirmHandler } from '../api/confirm';
import type { ConfirmRequest } from '../api/confirm';
import { useBackendStatus } from '../api/backend';
import { ErrorBoundary } from './ErrorBoundary';
import ApiErrorToasts from './ApiErrorToasts';
import { AppNotAnswering, BackendDownBanner, OfflineBanner } from './errorPages';

type DialogKind = 'menu' | 'flatten' | 'resume';

/** A kill-switch request the backend has not confirmed yet (055 → Loading):
 * resent every KILL_RESEND_MS under the same request_id until it answers. */
interface KillSend {
  action: 'entries' | 'flatten';
  requestId: string;
  since: number;
}

export function Layout() {
  const [state, setState] = useState<HeaderState | null>(null);
  const [dialog, setDialog] = useState<DialogKind | null>(null);
  const [positions, setPositions] = useState<PositionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ksSend, setKsSend] = useState<KillSend | null>(null);
  const [ksBanner, setKsBanner] = useState(false);
  const [ksToast, setKsToast] = useState<string | null>(null);
  const [bannerRefresh, setBannerRefresh] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuWrapRef = useRef<HTMLDivElement | null>(null);
  const liveStatus = useLiveStatus();
  const narrow = useMediaQuery('(max-width: 820px)');
  const location = useLocation();
  const authStatus = useAuthStatus();
  const backend = useBackendStatus();
  const [confirmReq, setConfirmReq] = useState<ConfirmRequest | null>(null);
  const ask = useAsk();
  // Each screen tells Ask what it is (spec → Ask the agent → Context).
  const screen = location.pathname === '/' ? 'dashboard' : location.pathname.split('/')[1];
  useEffect(() => {
    ask.register({
      screen,
      label: pageTitle(location.pathname),
      route: location.pathname,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname]);

  // Not signed in while auth is required → the sign-in page; a session-
  // expired cookie change is picked up on the next API call anyway.
  useEffect(() => {
    if (authStatus && authStatus.required && !authStatus.signed_in) {
      const next = window.location.pathname + window.location.search;
      window.location.assign(`/login?next=${encodeURIComponent(next)}`);
    }
  }, [authStatus]);

  // client.ts asks here when a write answers 403 confirm_required.
  useEffect(() => {
    registerConfirmHandler((req) => {
      setConfirmReq({
        action: req.action,
        resolve: (ok) => {
          req.resolve(ok);
          setConfirmReq(null);
        },
      });
    });
    return () => registerConfirmHandler(null);
  }, []);

  const refreshHeader = useCallback(() => {
    getHeaderState().then(setState).catch(console.error);
  }, []);

  const refreshAll = useCallback(() => {
    refreshHeader();
    setBannerRefresh((n) => n + 1);
  }, [refreshHeader]);

  // Kill switch (055 → Loading): the press is sent at once and resent
  // every KILL_RESEND_MS under one request_id — the backend runs it once.
  // A real HTTP answer stops the loop; a network failure keeps resending.
  // After KILL_BANNER_MS a critical banner shows while resends continue.
  useEffect(() => {
    if (!ksSend) return;
    let finished = false;
    const finish = (confirmed: boolean) => {
      if (finished) return;
      finished = true;
      const send = ksSend;
      setKsSend(null);
      setKsBanner(false);
      refreshAll();
      if (confirmed && Date.now() - send.since >= KILL_RESEND_MS) {
        setKsToast(
          `Kill switch on since ${displayTime(new Date(send.since).toISOString())}`,
        );
      }
    };
    const attempt = () => {
      postKillSwitch(ksSend.action, ksSend.requestId).then(
        () => finish(true),
        (e: Error) => {
          if (finished) return;
          if (e instanceof ApiError) {
            // The backend answered — a real failure, not a lost packet.
            finished = true;
            setKsSend(null);
            setKsBanner(false);
            setError(e.message);
          }
          // Otherwise the next interval tick resends the same request_id.
        },
      );
    };
    attempt();
    const resend = window.setInterval(attempt, KILL_RESEND_MS);
    const bannerTimer = window.setTimeout(() => setKsBanner(true), KILL_BANNER_MS);
    return () => {
      finished = true;
      window.clearInterval(resend);
      window.clearTimeout(bannerTimer);
    };
  }, [ksSend, refreshAll]);

  // The confirmation toast closes itself.
  useEffect(() => {
    if (!ksToast) return;
    const t = window.setTimeout(() => setKsToast(null), TOAST_AUTO_CLOSE_MS);
    return () => window.clearTimeout(t);
  }, [ksToast]);

  // Topics that change the header strip or banners; page data is fetched by
  // the pages themselves via their own useLiveUpdates subscriptions.
  useLiveUpdates(
    ['header', 'banners', 'positions', 'orders', 'approvals'],
    refreshAll,
  );
  const attentionRes = useApiResource(getAttention);
  useLiveUpdates(['attention'], attentionRes.reload);
  const needsYou = narrow ? 0 : (attentionRes.data?.count ?? 0);
  // BannerStrip fetches on mount; only the header needs a fetch here.
  useEffect(() => {
    refreshHeader();
  }, [refreshHeader]);

  // The backend came back after an outage — refetch the header and the
  // banner strip; pages refetch their own resources on the same change.
  const wasDown = useRef(false);
  useEffect(() => {
    if (wasDown.current && !backend.down) refreshAll();
    wasDown.current = backend.down;
  }, [backend.down, refreshAll]);
  const [prevPath, setPrevPath] = useState(location.pathname);
  if (location.pathname !== prevPath) {
    setPrevPath(location.pathname);
    setMenuOpen(false);
  }

  // Browser tab shows the page and the agent's state (open, paused, kill
  // switch) — docs/SPEC.md, Interaction and states.
  useEffect(() => {
    document.title = documentTitle(location.pathname, state);
  }, [location.pathname, state]);

  // Mobile menu overlay (design/DESIGN.md, Interaction rules): Escape
  // closes, Tab stays inside the panel, and focus returns to the control
  // that opened it.
  useEffect(() => {
    if (!menuOpen) return;
    const previous = document.activeElement as HTMLElement | null;
    const root = menuWrapRef.current;
    root?.querySelector<HTMLElement>('a[href], button')?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setMenuOpen(false);
        return;
      }
      if (e.key !== 'Tab' || !root) return;
      const items = root.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled])',
      );
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        last.focus();
        e.preventDefault();
      } else if (!e.shiftKey && document.activeElement === last) {
        first.focus();
        e.preventDefault();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      previous?.focus();
    };
  }, [menuOpen]);

  const onKillSwitchButton = () => {
    // While a request is unconfirmed the button is busy; a second press
    // must not start a second request (055 → Kill switch).
    if (ksSend) return;
    if (state?.kill_switch) {
      setDialog('resume');
    } else {
      setDialog('menu');
    }
  };

  const beginKillSend = (action: 'entries' | 'flatten') => {
    setDialog(null);
    setKsSend({
      action,
      requestId: crypto.randomUUID(),
      since: Date.now(),
    });
  };

  const onMenuConfirm = (scope: 'entries' | 'flatten') => {
    if (scope === 'entries') {
      beginKillSend('entries');
    } else {
      getPositions()
        .then((p) => {
          setPositions(p);
          setDialog('flatten');
        })
        .catch((e: Error) => setError(e.message));
    }
  };

  const onFlattenConfirm = () => {
    beginKillSend('flatten');
  };

  // Pause/Resume is one click in both directions — no dialog, no reason.
  const onPauseToggle = () => {
    const call = state?.paused ? postPauseResume : postPause;
    call()
      .then(() => refreshAll())
      .catch((e: Error) => setError(e.message));
  };

  const onResumeConfirm = (reason: string) => {
    postKillSwitchResume(reason)
      .then(() => {
        setDialog(null);
        refreshAll();
      })
      .catch((e: Error) => setError(e.message));
  };

  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'stretch',
        minHeight: '100%',
        background: 'var(--sa-bg)',
      }}
    >
      {!narrow && <Sidebar authRequired={authStatus?.required ?? false} />}
      {narrow && menuOpen && (
        <div ref={menuWrapRef}>
          <Sidebar overlay onNavigate={() => setMenuOpen(false)} authRequired={authStatus?.required ?? false} />
        </div>
      )}
      <div style={{ flex: '999 1 560px', minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        {narrow && (
          <PhoneBar
            title={pageTitle(location.pathname)}
            needsYou={attentionRes.data?.count ?? 0}
            onMenu={() => setMenuOpen((v) => !v)}
            onAsk={ask.toggle}
            menuOpen={menuOpen}
          />
        )}
        <Header
          state={state}
          title={pageTitle(location.pathname)}
          onKillSwitch={onKillSwitchButton}
          onPauseToggle={onPauseToggle}
          killBusy={ksSend !== null}
          killBusyLabel={ksSend?.action === 'flatten' ? 'Flattening…' : 'Turning on…'}
          needsYou={needsYou}
          narrow={narrow}
        />
        <main style={{ flex: 1, padding: 'var(--sa-space-6)' }}>
          {liveStatus === 'paused' && (
            <div
              style={{
                marginBottom: 'var(--sa-space-3)',
                fontSize: 'var(--sa-text-sm)',
                color: 'var(--sa-text-muted)',
              }}
            >
              Live updates paused — refreshing every minute.
            </div>
          )}
          {error && (
            <div style={{ marginBottom: 'var(--sa-space-4)' }}>
              <Banner variant="negative">Action failed: {error}</Banner>
            </div>
          )}
          {ksBanner && (
            <div style={{ marginBottom: 'var(--sa-space-4)' }}>
              <Banner variant="negative" role="alert">
                Kill switch not confirmed
              </Banner>
            </div>
          )}
          <BannerStrip
            refreshKey={bannerRefresh}
            onResumeRequest={() => setDialog('resume')}
            onFlattenRequest={() => setDialog('flatten')}
            onChanged={refreshAll}
          />
          {backend.offline && <OfflineBanner />}
          {backend.down && backend.everUp && backend.since && (
            <BackendDownBanner since={backend.since} />
          )}
          {backend.down && !backend.everUp ? (
            <AppNotAnswering />
          ) : (
            <ErrorBoundary key={location.pathname}>
              <Outlet />
            </ErrorBoundary>
          )}
        </main>
      </div>
      <AskDrawer />
      <NotificationToasts />
      <ApiErrorToasts />
      {ksToast && (
        <div
          role="status"
          style={{
            position: 'fixed',
            right: 'var(--sa-space-5)',
            bottom: 'var(--sa-space-5)',
            zIndex: 70,
            maxWidth: 420,
            padding: '12px 16px',
            borderRadius: 'var(--sa-radius-md)',
            background: 'var(--sa-positive-bg)',
            border: '1px solid var(--sa-positive-border)',
            color: 'var(--sa-positive)',
            fontSize: 'var(--sa-text-md)',
            lineHeight: 1.5,
          }}
        >
          {ksToast}
        </div>
      )}

      <KillSwitchDialog
        open={dialog === 'menu'}
        onCancel={() => setDialog(null)}
        onConfirm={onMenuConfirm}
      />
      <FlattenConfirmDialog
        open={dialog === 'flatten'}
        positions={positions?.open ?? []}
        onCancel={() => setDialog(null)}
        onConfirm={onFlattenConfirm}
      />
      <ResumeDialog
        open={dialog === 'resume'}
        since={null}
        nextCycle={state?.session.next_cycle ?? null}
        onCancel={() => setDialog(null)}
        onConfirm={onResumeConfirm}
      />
      <ConfirmYouDialog request={confirmReq} />
    </div>
  );
}
