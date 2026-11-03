import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { ReactNode } from 'react';

import type { AskContext } from '../api/ask';

interface AskState {
  open: boolean;
  context: AskContext | null;
  /** The context the current thread was started with (kept per chat). */
  setContext: (ctx: AskContext | null) => void;
  openDrawer: (ctx?: AskContext | null) => void;
  closeDrawer: () => void;
  /** Register the page's context (called by each screen on mount/change). */
  register: (ctx: AskContext | null) => void;
  /** The screen's own context (before the drawer overrides it). */
  pageContext: AskContext | null;
  toggle: () => void;
}

const Ctx = createContext<AskState | null>(null);

export function AskProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [context, setContext] = useState<AskContext | null>(null);
  const [pageContext, setPageContext] = useState<AskContext | null>(null);
  const lastFocus = useRef<HTMLElement | null>(null);

  const openDrawer = useCallback(
    (ctx?: AskContext | null) => {
      lastFocus.current = document.activeElement as HTMLElement | null;
      if (ctx !== undefined) setContext(ctx);
      setOpen(true);
    },
    [],
  );

  const closeDrawer = useCallback(() => {
    setOpen(false);
    lastFocus.current?.focus?.();
  }, []);

  const toggle = useCallback(() => {
    if (open) closeDrawer();
    else openDrawer(undefined);
  }, [open, openDrawer, closeDrawer]);

  const register = useCallback((ctx: AskContext | null) => {
    setPageContext(ctx);
    setContext((prev) => prev ?? ctx);
  }, []);

  // Ctrl/⌘+J toggles the drawer from anywhere.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'j') {
        e.preventDefault();
        toggle();
      }
      if (e.key === 'Escape' && open) closeDrawer();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, toggle, closeDrawer]);

  const value = useMemo<AskState>(
    () => ({
      open,
      context,
      setContext,
      openDrawer,
      closeDrawer,
      register,
      pageContext,
      toggle,
    }),
    [open, context, openDrawer, closeDrawer, register, pageContext, toggle],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAsk(): AskState {
  const ctx = useContext(Ctx);
  if (!ctx) {
    // Outside the provider (tests): inert defaults.
    return {
      open: false,
      context: null,
      setContext: () => {},
      openDrawer: () => {},
      closeDrawer: () => {},
      register: () => {},
      pageContext: null,
      toggle: () => {},
    };
  }
  return ctx;
}

/** A page calls this once per context change to describe itself to Ask. */
// eslint-disable-next-line react-refresh/only-export-components
export function useAskPageContext(ctx: AskContext | null, deps: unknown[] = []) {
  const ask = useAsk();
  useEffect(() => {
    ask.register(ctx);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
