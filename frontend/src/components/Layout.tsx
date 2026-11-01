import { useCallback, useEffect, useRef, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';

import { useLiveStatus, useLiveUpdates } from '../api/liveContext';
import { useApiResource } from '../api/useResource';
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
import { PhoneBar } from './PhoneBar';
import { Sidebar } from './Sidebar';
import { ConfirmYouDialog } from './ConfirmYouDialog';
import { registerConfirmHandler, useAuthStatus } from '../api/auth';
import type { ConfirmRequest } from '../api/auth';
import { useBackendStatus } from '../api/backend';
import { ErrorBoundary } from './ErrorBoundary';
import ApiErrorToasts from './ApiErrorToasts';
import { AppNotAnswering, BackendDownBanner, OfflineBanner } from './errorPages';

type DialogKind = 'menu' | 'flatten' | 'resume';

export function Layout() {
  const [state, setState] = useState<HeaderState | null>(null);
  const [dialog, setDialog] = useState<DialogKind | null>(null);
  const [positions, setPositions] = useState<PositionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [bannerRefresh, setBannerRefresh] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuWrapRef = useRef<HTMLDivElement | null>(null);
  const liveStatus = useLiveStatus();
  const narrow = useMediaQuery('(max-width: 820px)');
  const location = useLocation();
  const authStatus = useAuthStatus();
  const backend = useBackendStatus();
  const [confirmReq, setConfirmReq] = useState<ConfirmRequest | null>(null);

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
    if (state?.kill_switch) {
      setDialog('resume');
    } else {
      setDialog('menu');
    }
  };

  const onMenuConfirm = (scope: 'entries' | 'flatten') => {
    if (scope === 'entries') {
      postKillSwitch('entries')
        .then(() => {
          setDialog(null);
          refreshAll();
        })
        .catch((e: Error) => setError(e.message));
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
    postKillSwitch('flatten')
      .then(() => {
        setDialog(null);
        refreshAll();
      })
      .catch((e: Error) => setError(e.message));
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
            menuOpen={menuOpen}
          />
        )}
        <Header
          state={state}
          title={pageTitle(location.pathname)}
          onKillSwitch={onKillSwitchButton}
          onPauseToggle={onPauseToggle}
          needsYou={needsYou}
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
          <BannerStrip
            refreshKey={bannerRefresh}
            onResumeRequest={() => setDialog('resume')}
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
      <NotificationToasts />
      <ApiErrorToasts />

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
