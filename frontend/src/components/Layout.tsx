import { useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';

import { getHeaderState } from '../api/state';
import type { HeaderState } from '../types/api';
import { Header } from './Header';
import { Sidebar } from './Sidebar';

export function Layout() {
  const [state, setState] = useState<HeaderState | null>(null);

  useEffect(() => {
    getHeaderState().then(setState).catch(console.error);
  }, []);

  return (
    <div style={{ display: 'flex', minHeight: '100%', background: 'var(--sa-bg)' }}>
      <Sidebar />
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <Header state={state} />
        <main style={{ flex: 1, padding: 'var(--sa-space-5)' }}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
