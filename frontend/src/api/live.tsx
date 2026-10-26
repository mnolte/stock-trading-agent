import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import {
  DEBOUNCE_MS,
  FALLBACK_POLL_MS,
  LiveContext,
  RECONNECT_MS,
} from './liveContext';
import type { LiveStatus, Subscription, Topic } from './liveContext';

function wsUrl(): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${window.location.host}/ws/ws`;
}

/** Owns the /ws/ws socket. Pushes {"type":"update","topics":[...]} trigger a
 * 300 ms debounced refetch of just those topics; a reconnect refetches
 * everything; while the socket is down every subscriber is polled every 60 s.
 */
export function LiveUpdatesProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<LiveStatus>('paused');
  const subs = useRef(new Map<number, Subscription>());
  const nextId = useRef(0);
  const pending = useRef(new Set<Topic>());
  const debounce = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const flush = useCallback(() => {
    const topics = pending.current;
    pending.current = new Set();
    const delivered = new Set<Topic>();
    subs.current.forEach((sub) => {
      for (const topic of topics) {
        if (sub.topics.has(topic)) {
          delivered.add(topic);
          sub.cb();
          break;
        }
      }
    });
    // Topics nobody is subscribed to yet stay pending — a component that
    // mounts after the message arrived (lazy route, Suspense fallback)
    // must still see its wake-up instead of dropping it here.
    for (const topic of topics) {
      if (!delivered.has(topic)) pending.current.add(topic);
    }
  }, []);

  const fireAll = useCallback(() => {
    subs.current.forEach((sub) => sub.cb());
  }, []);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let stopped = false;
    let reconnect: ReturnType<typeof setTimeout> | undefined;
    let poll: ReturnType<typeof setInterval> | undefined;

    const connect = () => {
      socket = new WebSocket(wsUrl());
      socket.onopen = () => {
        setStatus('live');
        if (poll !== undefined) {
          clearInterval(poll);
          poll = undefined;
        }
        // Full refetch after (re)connect.
        fireAll();
      };
      socket.onmessage = (event: MessageEvent) => {
        try {
          const msg = JSON.parse(String(event.data)) as {
            type?: string;
            topics?: string[];
          };
          if (msg.type !== 'update' || !Array.isArray(msg.topics)) return;
          for (const topic of msg.topics) {
            pending.current.add(topic as Topic);
          }
          clearTimeout(debounce.current);
          debounce.current = setTimeout(flush, DEBOUNCE_MS);
        } catch {
          // Ignore malformed frames.
        }
      };
      socket.onclose = () => {
        setStatus('paused');
        if (stopped) return;
        if (poll === undefined) {
          poll = setInterval(fireAll, FALLBACK_POLL_MS);
        }
        reconnect = setTimeout(connect, RECONNECT_MS);
      };
      socket.onerror = () => socket?.close();
    };

    connect();
    return () => {
      stopped = true;
      socket?.close();
      clearTimeout(reconnect);
      clearTimeout(debounce.current);
      if (poll !== undefined) clearInterval(poll);
    };
  }, [fireAll, flush]);

  const subscribe = useCallback((topics: Topic[], cb: () => void) => {
    const id = nextId.current++;
    subs.current.set(id, { topics: new Set(topics), cb });
    // Deliver an update that arrived before this subscriber existed.
    for (const topic of pending.current) {
      if (topics.includes(topic)) {
        pending.current.delete(topic);
        cb();
        break;
      }
    }
    return () => {
      subs.current.delete(id);
    };
  }, []);

  return (
    <LiveContext.Provider value={{ status, subscribe }}>
      {children}
    </LiveContext.Provider>
  );
}

export type { Topic } from './liveContext';
