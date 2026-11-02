import { get, post } from './client';

export interface AskContext {
  screen: string;
  label: string;
  record_ref?: string;
  summary?: string;
  route?: string;
}

export interface AskStep {
  call_id?: string;
  tool: string;
  arguments?: Record<string, unknown>;
  label: string;
  error?: string | null;
  ms?: number;
  rows?: number | null;
  truncated?: boolean;
  running?: boolean;
}

export interface AskMessage {
  id: number;
  role: 'user' | 'assistant';
  text: string;
  steps?: AskStep[] | null;
  references?: string[] | null;
  unverified_numbers?: { text: string; start: number; end: number }[] | null;
  model?: string | null;
  cost?: string | null;
  status: 'ok' | 'stopped' | 'failed' | 'cap';
  error?: string | null;
  created_at: string;
}

export interface AskThread {
  id: number;
  title: string;
  context: AskContext | null;
  created_at: string;
  updated_at: string;
  questions: number | null;
  keep_days?: number;
}

export interface AskNote {
  id: number;
  text: string;
  references: string[];
  source_thread_id: number | null;
  source_message_id: number | null;
  source_chat: string | null;
  created_at: string;
  updated_at: string;
}

export interface AskStatus {
  enabled: boolean;
  reason?: string | null;
  model?: string;
  spent_usd?: string;
  cap_usd?: number;
  questions?: number;
  questions_cost_usd?: string;
  avg_cost_usd?: string | null;
}

export const getAskStatus = () => get<AskStatus>('/api/ask/status');
export const listThreads = (search?: string) =>
  get<{ threads: AskThread[]; keep_days: number }>(
    `/api/ask/threads${search ? `?search=${encodeURIComponent(search)}` : ''}`,
  );
export const getThread = (id: number) =>
  get<AskThread & { messages: AskMessage[] }>(`/api/ask/threads/${id}`);
export const createThread = (context: AskContext | null) =>
  post<AskThread>('/api/ask/threads', { context });
export const deleteThread = (id: number) =>
  fetch(`/api/ask/threads/${id}`, { method: 'DELETE' }).then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
  });
export const stopMessage = (id: number) =>
  post<{ ok: boolean }>(`/api/ask/messages/${id}/stop`, {});
export const listNotes = () =>
  get<{ notes: AskNote[]; count: number; max: number }>('/api/ask/notes');
export const createNote = (body: { text?: string; from_message?: number }) =>
  post<AskNote>('/api/ask/notes', body);
export const updateNote = (id: number, text: string) =>
  fetch(`/api/ask/notes/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  }).then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json() as Promise<AskNote>;
  });
export const deleteNote = (id: number) =>
  fetch(`/api/ask/notes/${id}`, { method: 'DELETE' }).then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
  });

export type AskEvent =
  | { event: 'step'; data: AskStep }
  | { event: 'delta'; data: { text: string } }
  | { event: 'done'; data: Record<string, unknown> & { status?: string } }
  | { event: 'error'; data: { message: string; id?: number } }
  | { event: 'refs'; data: Record<string, unknown> };

/** POST the question and read the text/event-stream. */
export async function askMessage(
  threadId: number,
  text: string,
  context: AskContext | null,
  onEvent: (e: AskEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const csrf = document.cookie.match(/(?:^|;\s*)sa_csrf=([^;]*)/)?.[1];
  const response = await fetch(`/api/ask/threads/${threadId}/messages`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
    },
    body: JSON.stringify({ text, context }),
    signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const err = new Error(
      body?.error?.message ?? body?.detail ?? `HTTP ${response.status}`,
    ) as Error & { status?: number };
    err.status = response.status;
    throw err;
  }
  const reader = response.body?.getReader();
  if (!reader) return;
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let event = '';
      let data = '';
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7).trim();
        if (line.startsWith('data: ')) data = line.slice(6);
      }
      if (event && data) {
        try {
          onEvent({ event: event as AskEvent['event'], data: JSON.parse(data) } as AskEvent);
        } catch {
          // ignore malformed event
        }
      }
    }
  }
}
