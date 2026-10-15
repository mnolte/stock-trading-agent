import { useEffect, useState } from 'react';

import { getWatchlist } from '../api/state';
import { Watchlist } from '../components/Watchlist';
import type { WatchlistResponse } from '../types/api';

export default function Dashboard() {
  const [watchlist, setWatchlist] = useState<WatchlistResponse | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    getWatchlist().then(setWatchlist).catch(console.error);
  }, []);

  return (
    <div style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}>
      <Watchlist
        rows={watchlist?.rows ?? []}
        selected={selected}
        onSelect={setSelected}
      />
    </div>
  );
}
