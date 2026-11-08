import { useEffect, useRef, useState } from 'react';

import { NEW_ROW_MS } from './timing';

/**
 * Ids that appeared since the previous settled list. A live refresh that
 * brings a new row returns it here for NEW_ROW_MS so the row can flash
 * `.sa-new` once; rows already seen never re-highlight.
 */
export function useNewIds<T extends string | number>(ids: readonly T[]): ReadonlySet<T> {
  const [fresh, setFresh] = useState<ReadonlySet<T>>(() => new Set());
  const seen = useRef<Set<T> | null>(null);
  const key = ids.join('');

  useEffect(() => {
    const prev = seen.current;
    seen.current = new Set(ids);
    if (prev === null) return;
    const added = ids.filter((id) => !prev.has(id));
    if (added.length === 0) return;
    setFresh(new Set(added));
    const timer = window.setTimeout(() => setFresh(new Set()), NEW_ROW_MS);
    return () => window.clearTimeout(timer);
    // `key` stands in for `ids`: the array identity changes every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return fresh;
}
