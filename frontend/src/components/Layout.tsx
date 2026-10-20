import { useCallback, useEffect, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';

import { useLiveStatus, useLiveUpdates } from '../api/liveContext';
import {
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
import { Header } from './Header';
import { FlattenConfirmDialog, KillSwitchDialog, ResumeDialog } from './KillSwitchDialogs';
import NotificationToasts from './NotificationToasts';
import { Sidebar } from './Sidebar';

type DialogKind = 'menu' | 'flatten' | 'resume';

function pageTitle(pathname: string): string {
  if (pathname.startsWith('/positions')) return 'Positions & orders';
  if (pathname.startsWith('/performance')) return 'Performance';
  if (pathname.startsWith('/inspector')) return 'LLM inspector';
  if (pathname.startsWith('/reviews')) return 'Reviews';
  if (pathname.startsWith('/strategies')) return 'Strategies';
  if (pathname.startsWith('/decisions')) return 'Decision detail';
  if (pathname.startsWith('/settings')) return 'Settings';
  if (pathname.startsWith('/states')) return 'States & dialogs';
  return 'Dashboard';
}

export function Layout() {
  const [state, setState] = useState<HeaderState | null>(null);
  const [dialog, setDialog] = useState<DialogKind | null>(null);
  const [positions, setPositions] = useState<PositionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [bannerRefresh, setBannerRefresh] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const liveStatus = useLiveStatus();
  const narrow = useMediaQuery('(max-width: 820px)');
  const location = useLocation();

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
  useEffect(refreshAll, [refreshAll]);
  useEffect(() => setMenuOpen(false), [location.pathname]);

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
      {!narrow && <Sidebar />}
      {narrow && menuOpen && (
        <Sidebar overlay onNavigate={() => setMenuOpen(false)} />
      )}
      <div style={{ flex: '999 1 560px', minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <Header
          state={state}
          title={pageTitle(location.pathname)}
          onKillSwitch={onKillSwitchButton}
          onPauseToggle={onPauseToggle}
          onMenu={narrow ? () => setMenuOpen((v) => !v) : undefined}
        />
        <main style={{ flex: 1, padding: 'var(--sa-space-5)' }}>
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
          <Outlet />
        </main>
      </div>
      <NotificationToasts />

      <KillSwitchDialog
        open={dialog === 'menu'}
        onCancel={() => setDialog(null)}
        onConfirm={onMenuConfirm}
      />
      <FlattenConfirmDialog
        open={dialog === 'flatten'}
        positions={positions?.open ?? []}
        dayTrades={positions?.day_trades_5d ?? 0}
        dayTradesMax={positions?.day_trades?.max ?? 3}
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
    </div>
  );
}
