import { useCallback, useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';

import {
  getHeaderState,
  getPositions,
  postKillSwitch,
  postKillSwitchResume,
} from '../api/state';
import type { HeaderState, PositionsResponse } from '../types/api';
import { Banner } from './Banner';
import { BannerStrip } from './BannerStrip';
import { Header } from './Header';
import { FlattenConfirmDialog, KillSwitchDialog, ResumeDialog } from './KillSwitchDialogs';
import { Sidebar } from './Sidebar';

type DialogKind = 'menu' | 'flatten' | 'resume';

export function Layout() {
  const [state, setState] = useState<HeaderState | null>(null);
  const [dialog, setDialog] = useState<DialogKind | null>(null);
  const [positions, setPositions] = useState<PositionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [bannerRefresh, setBannerRefresh] = useState(0);

  const refreshHeader = useCallback(() => {
    getHeaderState().then(setState).catch(console.error);
  }, []);

  useEffect(() => {
    refreshHeader();
    const timer = setInterval(refreshHeader, 10_000);
    return () => clearInterval(timer);
  }, [refreshHeader]);

  const refreshAll = useCallback(() => {
    refreshHeader();
    setBannerRefresh((n) => n + 1);
  }, [refreshHeader]);

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
      <Sidebar />
      <div style={{ flex: '999 1 560px', minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <Header state={state} onKillSwitch={onKillSwitchButton} />
        <main style={{ flex: 1, padding: 'var(--sa-space-5)' }}>
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

      <KillSwitchDialog
        open={dialog === 'menu'}
        onCancel={() => setDialog(null)}
        onConfirm={onMenuConfirm}
      />
      <FlattenConfirmDialog
        open={dialog === 'flatten'}
        positions={positions?.open ?? []}
        dayTrades={positions?.day_trades_5d ?? 0}
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
