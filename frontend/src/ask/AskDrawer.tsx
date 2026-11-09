import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';

import {
  askMessage,
  createNote,
  createThread,
  deleteNote,
  deleteThread,
  getAskStatus,
  getThread,
  listNotes,
  listThreads,
  stopMessage,
  updateNote,
} from '../api/ask';
import type {
  AskContext,
  AskMessage,
  AskNote,
  AskStatus,
  AskStep,
  AskThread,
} from '../api/ask';
import { useMediaQuery } from '../hooks/useMediaQuery';
import { useAsk } from './AskContext';
import { AskMarkdown } from './AskMarkdown';
import { refLink, suggestionsFor } from './askFormat';
import { fmtDay } from '../format';

type View = 'chat' | 'list';

interface Draft {
  threadId: number | null;
  messages: AskMessage[];
  streamingText: string;
  liveSteps: AskStep[];
  streaming: boolean;
  stopped: boolean;
  failed: string | null;
  failedQuestion: string | null;
  cap: boolean;
}

const EMPTY: Draft = {
  threadId: null,
  messages: [],
  streamingText: '',
  liveSteps: [],
  streaming: false,
  stopped: false,
  failed: null,
  failedQuestion: null,
  cap: false,
};

export function AskDrawer() {
  const ask = useAsk();
  const location = useLocation();
  const phone = useMediaQuery('(max-width: 820px)');
  const docked = useMediaQuery('(min-width: 1280px)');
  const [view, setView] = useState<View>('chat');
  const [tab, setTab] = useState<'chats' | 'notes'>('chats');
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [threads, setThreads] = useState<AskThread[]>([]);
  const [notes, setNotes] = useState<AskNote[]>([]);
  const [status, setStatus] = useState<AskStatus | null>(null);
  const [search, setSearch] = useState('');
  const [input, setInput] = useState('');
  const [noteEdit, setNoteEdit] = useState<{ id: number | null; text: string; from?: number } | null>(null);
  const [noteSaveError, setNoteSaveError] = useState<string | null>(null);
  const [contextCleared, setContextCleared] = useState(false);
  const [threadContext, setThreadContext] = useState<AskContext | null>(null);
  const [noteMax, setNoteMax] = useState(100);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const logRef = useRef<HTMLDivElement | null>(null);
  const queued = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  const activeContext = contextCleared ? null : (threadContext ?? ask.pageContext);
  const navigated = threadContext?.route && threadContext.route !== location.pathname;

  // Focus the composer on open; the log announces only finished answers.
  useEffect(() => {
    if (ask.open) {
      getAskStatus().then(setStatus).catch(() => setStatus(null));
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [ask.open]);

  useEffect(() => {
    if (ask.open && draft.threadId === null && !draft.streaming) {
      setThreadContext(ask.pageContext);
      setContextCleared(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ask.open]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [draft.messages, draft.streamingText, draft.liveSteps]);

  const send = useCallback(
    async (rawText?: string) => {
      const text = (rawText ?? input).trim();
      if (!text || text.length > 2000) return;
      if (draft.streaming) {
        queued.current = text;
        setInput('');
        return;
      }
      setInput('');
      setNoteSaveError(null);
      let threadId = draft.threadId;
      if (threadId === null) {
        const t = await createThread(activeContext);
        threadId = t.id;
      }
      const userMsg: AskMessage = {
        id: -(Date.now()),
        role: 'user',
        text,
        status: 'ok',
        created_at: new Date().toISOString(),
      };
      setDraft((d) => ({
        ...d,
        threadId,
        messages: [...d.messages, userMsg],
        streaming: true,
        streamingText: '',
        liveSteps: [],
        failed: null,
        failedQuestion: null,
        cap: false,
        stopped: false,
      }));
      abort.current = new AbortController();
      const ctx = activeContext;
      let buffer = '';
      const steps: AskStep[] = [];
      try {
        await askMessage(
          threadId,
          text,
          ctx,
          (ev) => {
            if (ev.event === 'step') {
              steps.push(ev.data);
              setDraft((d) => ({ ...d, liveSteps: [...steps] }));
            } else if (ev.event === 'delta') {
              buffer += ev.data.text;
              setDraft((d) => ({ ...d, streamingText: buffer }));
            } else if (ev.event === 'done') {
              const done = ev.data;
              if (done.status === 'cap') {
                setDraft((d) => ({ ...d, streaming: false, cap: true }));
                return;
              }
              const msg: AskMessage = {
                id: Number(done.id ?? -1),
                role: 'assistant',
                text: String(done.text ?? buffer),
                steps: steps.length ? [...steps] : null,
                references: (done.references as string[]) ?? [],
                unverified_numbers:
                  (done.unverified_numbers as AskMessage['unverified_numbers']) ?? null,
                model: (done.model as string) ?? null,
                cost: (done.cost as string) ?? null,
                status: (done.status as AskMessage['status']) ?? 'ok',
                error: (done.note as string) ?? null,
                created_at: String(done.as_of ?? new Date().toISOString()),
              };
              setDraft((d) => ({
                ...d,
                streaming: false,
                streamingText: '',
                liveSteps: [],
                messages: [...d.messages, msg],
              }));
            } else if (ev.event === 'error') {
              setDraft((d) => ({
                ...d,
                streaming: false,
                failed: ev.data.message || 'The answer failed.',
                failedQuestion: text,
              }));
            }
          },
          abort.current.signal,
        );
      } catch (e) {
        if ((e as Error).name === 'AbortError') return;
        setDraft((d) => ({
          ...d,
          streaming: false,
          failed: (e as Error).message,
          failedQuestion: text,
        }));
      } finally {
        setDraft((d) => ({ ...d, streaming: false }));
        if (queued.current) {
          const next = queued.current;
          queued.current = null;
          setTimeout(() => void send(next), 0);
        }
      }
    },
    [draft.threadId, draft.streaming, input, activeContext],
  );

  const stop = useCallback(async () => {
    abort.current?.abort();
    const running = draft.messages[draft.messages.length - 1];
    setDraft((d) => ({
      ...d,
      streaming: false,
      stopped: true,
      messages: d.streamingText
        ? [
            ...d.messages,
            {
              id: -(Date.now()),
              role: 'assistant' as const,
              text: d.streamingText,
              steps: d.liveSteps.length ? [...d.liveSteps] : null,
              status: 'stopped' as const,
              created_at: new Date().toISOString(),
            },
          ]
        : d.messages,
      streamingText: '',
      liveSteps: [],
    }));
    if (running && running.id > 0) await stopMessage(running.id).catch(() => {});
  }, [draft]);

  const openThread = useCallback(async (id: number) => {
    const t = await getThread(id);
    setThreadContext(t.context);
    setContextCleared(false);
    setDraft({ ...EMPTY, threadId: t.id, messages: t.messages });
    setView('chat');
  }, []);

  const loadList = useCallback(async () => {
    const [t, n] = await Promise.all([listThreads(search || undefined), listNotes()]);
    setThreads(t.threads);
    setNotes(n.notes);
    setNoteMax(n.max);
  }, [search]);

  useEffect(() => {
    if (view === 'list') void loadList();
  }, [view, loadList]);

  if (!ask.open) return null;

  const enabled = status?.enabled ?? true;
  const capReached = draft.cap || (status && !status.enabled && (status.reason ?? '').includes('month'));
  const disabledReason =
    status && !status.enabled && !capReached ? status.reason : null;
  const suggestions = suggestionsFor(
    activeContext?.screen ?? 'default',
  );
  const today = new Date().toDateString();
  const todayThreads = threads.filter((t) => new Date(t.updated_at).toDateString() === today);
  const olderThreads = threads.filter((t) => new Date(t.updated_at).toDateString() !== today);

  return (
    <aside
      aria-label="Ask the agent"
      className={
        phone
          ? 'ask-drawer ask-drawer-phone'
          : docked
            ? 'ask-drawer ask-drawer-docked'
            : 'ask-drawer ask-drawer-overlay'
      }
    >
      <div className="ask-head">
        <span className="ask-icon" aria-hidden>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 5h16v11H9l-5 4z" />
            <line x1="8" y1="9" x2="16" y2="9" />
            <line x1="8" y1="12" x2="13" y2="12" />
          </svg>
        </span>
        <div className="ask-title">
          <span>Ask the agent</span>
          <small>
            {view === 'list'
              ? 'Earlier chats'
              : draft.threadId
                ? threads.find((t) => t.id === draft.threadId)?.title ?? 'Chat'
                : 'New chat'}
          </small>
        </div>
        <button
          type="button"
          aria-label="Earlier chats"
          title="Earlier chats"
          className="ask-head-btn"
          onClick={() => setView(view === 'list' ? 'chat' : 'list')}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="9" />
            <polyline points="12 7 12 12 15 14" />
          </svg>
        </button>
        <button
          type="button"
          className="ask-head-btn ask-new"
          title="New chat"
          onClick={() => {
            setDraft(EMPTY);
            setView('chat');
            setThreadContext(ask.pageContext);
            setContextCleared(false);
          }}
        >
          New
        </button>
        <button
          type="button"
          aria-label="Close"
          title="Close (Esc)"
          className="ask-head-btn"
          onClick={ask.closeDrawer}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="6" y1="6" x2="18" y2="18" />
            <line x1="18" y1="6" x2="6" y2="18" />
          </svg>
        </button>
      </div>

      {view === 'chat' && (
        <div className="ask-context">
          <span>About</span>
          {activeContext ? (
            <span className="ask-chip">
              {activeContext.label}
              {navigated ? (
                <button
                  type="button"
                  className="ask-chip-x"
                  onClick={() => {
                    setThreadContext(ask.pageContext);
                    setContextCleared(false);
                  }}
                >
                  Switch to this page
                </button>
              ) : (
                <button
                  type="button"
                  aria-label="Ask about the whole app instead"
                  className="ask-chip-x"
                  onClick={() => {
                    setContextCleared(true);
                    setThreadContext(null);
                  }}
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <line x1="6" y1="6" x2="18" y2="18" />
                    <line x1="18" y1="6" x2="6" y2="18" />
                  </svg>
                </button>
              )}
            </span>
          ) : (
            <span className="ask-chip">Whole app</span>
          )}
        </div>
      )}

      <div role="log" aria-live="polite" className="ask-log" ref={logRef}>
        {view === 'list' ? (
          <ListPanel
            tab={tab}
            setTab={setTab}
            search={search}
            setSearch={setSearch}
            todayThreads={todayThreads}
            olderThreads={olderThreads}
            notes={notes}
            noteMax={noteMax}
            openThread={openThread}
            removeThread={async (id) => {
              await deleteThread(id);
              void loadList();
            }}
            onWriteNote={() => setNoteEdit({ id: null, text: '' })}
            onEditNote={(n) => setNoteEdit({ id: n.id, text: n.text })}
            onDeleteNote={async (id) => {
              await deleteNote(id);
              void loadList();
            }}
            onNoteRef={() => setTab('notes')}
          />
        ) : draft.messages.length === 0 && !draft.streaming ? (
          <div className="ask-suggest">
            <div className="ask-suggest-title">
              {activeContext?.record_ref ? 'Ask about this' : 'Ask about this page'}
            </div>
            <p>
              The agent can read its decisions, positions, orders, results, reviews,
              logs and settings. It answers with links to what it used, and never
              changes anything.
            </p>
            {suggestions.map((s) => (
              <button key={s} type="button" className="ask-suggestion" onClick={() => void send(s)}>
                {s}
              </button>
            ))}
          </div>
        ) : (
          <Conversation
            draft={draft}
            onNoteRef={() => setTab('notes')}
            onSaveNote={(m) =>
              setNoteEdit({
                id: null,
                text: (m.text || '').split('\n')[0].slice(0, 1000),
                from: m.id > 0 ? m.id : undefined,
              })
            }
            onCopy={(m) => void navigator.clipboard?.writeText(m.text)}
            onRetry={(q) => void send(q)}
          />
        )}
      </div>

      {noteEdit && (
        <div className="ask-note-edit" role="dialog" aria-label="Save note">
          <textarea
            aria-label="Note text"
            rows={3}
            value={noteEdit.text}
            maxLength={1000}
            onChange={(e) => setNoteEdit({ ...noteEdit, text: e.target.value })}
          />
          <div className="ask-note-edit-row">
            <span>{noteEdit.text.length}/1,000</span>
            {noteSaveError && <span className="ask-err">{noteSaveError}</span>}
            <button
              type="button"
              className="ask-head-btn"
              onClick={() => setNoteEdit(null)}
            >
              Cancel
            </button>
            <button
              type="button"
              className="ask-send"
              onClick={async () => {
                try {
                  if (noteEdit.id) await updateNote(noteEdit.id, noteEdit.text);
                  else await createNote({ text: noteEdit.text, from_message: noteEdit.from });
                  setNoteEdit(null);
                  setNoteSaveError(null);
                  void loadList();
                } catch (e) {
                  setNoteSaveError((e as Error).message);
                }
              }}
            >
              Save note
            </button>
          </div>
        </div>
      )}

      <div className="ask-composer">
        {capReached ? (
          <div className="ask-cap">
            {status?.reason ??
              `Ask has used its monthly cap. It starts again on the 1st, or raise it in `}
            <Link to="/settings">Settings</Link>. Your chats stay readable.
          </div>
        ) : disabledReason ? (
          <div className="ask-cap">{disabledReason}</div>
        ) : (
          <>
            <div className="ask-input-row">
              <textarea
                ref={inputRef}
                aria-label="Your question"
                rows={2}
                placeholder={
                  activeContext?.record_ref ? 'Ask about this…' : 'Ask about this page…'
                }
                value={input}
                maxLength={2000}
                disabled={!enabled && !capReached}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    void send();
                  }
                }}
              />
              {draft.streaming ? (
                <button type="button" aria-label="Stop the answer" className="ask-stop" onClick={() => void stop()}>
                  <span className="ask-stop-sq" />
                  Stop
                </button>
              ) : (
                <button
                  type="button"
                  aria-label="Send"
                  className="ask-send"
                  disabled={!input.trim() || input.length > 2000}
                  onClick={() => void send()}
                >
                  Send
                </button>
              )}
            </div>
            <div className="ask-hint">
              <span>Read-only: it looks things up and never changes anything.</span>
              <span>Enter to send · Shift+Enter new line</span>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}

function Conversation({
  draft,
  onNoteRef,
  onSaveNote,
  onCopy,
  onRetry,
}: {
  draft: Draft;
  onNoteRef: (ref: string) => void;
  onSaveNote: (m: AskMessage) => void;
  onCopy: (m: AskMessage) => void;
  onRetry: (q: string) => void;
}) {
  return (
    <>
      {draft.messages.map((m) =>
        m.role === 'user' ? (
          <div key={m.id} className="ask-user">
            {m.text}
          </div>
        ) : (
          <div key={m.id} className="ask-answer">
            {m.steps?.length ? (
              <details className="ask-steps">
                <summary>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="11" cy="11" r="6" />
                    <line x1="16" y1="16" x2="21" y2="21" />
                  </svg>
                  Looked up {m.steps.length} thing{m.steps.length === 1 ? '' : 's'}
                </summary>
                <div className="ask-steps-list">
                  {m.steps.map((s, i) => (
                    <div key={i} className="ask-step">
                      <span className={s.error ? 'ask-step-err' : 'ask-step-ok'}>
                        {s.error ? '!' : '✓'}
                      </span>
                      <span>{s.label}</span>
                    </div>
                  ))}
                </div>
              </details>
            ) : null}
            {m.text ? (
              <AskMarkdown text={m.text} unverified={m.unverified_numbers ?? []} onNoteRef={onNoteRef} />
            ) : null}
            {(m.references?.length ?? 0) > 0 && (
              <div className="ask-refs">
                {m.references!.map((r) => (
                  <Link key={r} to={refLink(r).href} className="ask-ref">
                    {refLink(r).label}
                  </Link>
                ))}
              </div>
            )}
            <div className="ask-footer">
              {m.status === 'stopped' && <span>Stopped by you · </span>}
              {m.status === 'failed' && <span className="ask-err">Failed · </span>}
              {m.status === 'cap' && <span>{m.error}</span>}
              {m.error && m.status === 'ok' && <span>{m.error} · </span>}
              {(m.unverified_numbers?.length ?? 0) > 0 && (
                <span className="ask-amber">
                  {m.unverified_numbers!.length} number
                  {m.unverified_numbers!.length === 1 ? '' : 's'} not found in the data
                  {' · '}
                </span>
              )}
              {m.model && <span>{m.model} · </span>}
              {m.steps?.length ? <span>{m.steps.length} lookups · </span> : null}
              {m.cost && <span>${Number(m.cost).toFixed(3)} · </span>}
              <span>data as of {shortTime(m.created_at)}</span>
              <span style={{ flex: 1 }} />
              {m.status === 'ok' && m.id > 0 && (
                <button type="button" className="ask-foot-link" onClick={() => onSaveNote(m)}>
                  Save as note
                </button>
              )}
              <button type="button" className="ask-foot-link" onClick={() => onCopy(m)}>
                Copy
              </button>
              <Link to="/inspector" className="ask-foot-link">
                Model calls
              </Link>
            </div>
          </div>
        ),
      )}
      {draft.streaming && (
        <div className="ask-answer">
          {draft.liveSteps.length ? (
            <details open className="ask-steps">
              <summary>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="11" cy="11" r="6" />
                  <line x1="16" y1="16" x2="21" y2="21" />
                </svg>
                Looking things up · {draft.liveSteps.length}
              </summary>
              <div className="ask-steps-list">
                {draft.liveSteps.map((s, i) => (
                  <div key={i} className="ask-step">
                    <span className={i === draft.liveSteps.length - 1 ? 'ask-step-run' : 'ask-step-ok'}>
                      {i === draft.liveSteps.length - 1 ? '●' : '✓'}
                    </span>
                    <span>{s.label}</span>
                  </div>
                ))}
              </div>
            </details>
          ) : null}
          {draft.streamingText && (
            <span className="ask-stream">
              <AskMarkdown text={draft.streamingText} onNoteRef={onNoteRef} />
              <span className="ask-caret" />
            </span>
          )}
        </div>
      )}
      {draft.failed && (
        <div role="alert" className="ask-failed">
          The answer failed: {draft.failed} Your question is kept; Retry asks it again.
          <button type="button" onClick={() => draft.failedQuestion && onRetry(draft.failedQuestion)}>
            Retry
          </button>
        </div>
      )}
    </>
  );
}

function ListPanel({
  tab,
  setTab,
  search,
  setSearch,
  todayThreads,
  olderThreads,
  notes,
  noteMax,
  openThread,
  removeThread,
  onWriteNote,
  onEditNote,
  onDeleteNote,
  onNoteRef,
}: {
  tab: 'chats' | 'notes';
  setTab: (t: 'chats' | 'notes') => void;
  search: string;
  setSearch: (s: string) => void;
  todayThreads: AskThread[];
  olderThreads: AskThread[];
  notes: AskNote[];
  noteMax: number;
  openThread: (id: number) => void;
  removeThread: (id: number) => void;
  onWriteNote: () => void;
  onEditNote: (n: AskNote) => void;
  onDeleteNote: (id: number) => void;
  onNoteRef: (ref: string) => void;
}) {
  return (
    <div className="ask-list">
      <div role="tablist" className="ask-tabs">
        <button type="button" role="tab" aria-selected={tab === 'chats'} className={tab === 'chats' ? 'ask-tab on' : 'ask-tab'} onClick={() => setTab('chats')}>
          Chats
        </button>
        <button type="button" role="tab" aria-selected={tab === 'notes'} className={tab === 'notes' ? 'ask-tab on' : 'ask-tab'} onClick={() => setTab('notes')}>
          Notes · {notes.length}
        </button>
      </div>
      {tab === 'chats' ? (
        <>
          <input
            aria-label="Search chats"
            placeholder="Search chats"
            className="ask-search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {todayThreads.length > 0 && <div className="ask-day">Today</div>}
          {todayThreads.map((t) => (
            <ThreadRow key={t.id} t={t} onOpen={openThread} onDelete={removeThread} />
          ))}
          {olderThreads.length > 0 && <div className="ask-day">Earlier</div>}
          {olderThreads.map((t) => (
            <ThreadRow key={t.id} t={t} onOpen={openThread} onDelete={removeThread} />
          ))}
          <div className="ask-keepline">
            Chats are kept {90} days. Each one remembers the page it was started on;
            opening it again does not change the page you are on.
          </div>
        </>
      ) : (
        <>
          <button type="button" className="ask-note-add" onClick={onWriteNote}>
            + Write a note
          </button>
          {notes.map((n) => (
            <div key={n.id} className="ask-note">
              <div className="ask-note-text">
                <AskMarkdown text={n.text} onNoteRef={onNoteRef} />
              </div>
              {n.references.length > 0 && (
                <div className="ask-refs">
                  {n.references.map((r) => (
                    <Link key={r} to={refLink(r).href} className="ask-ref">
                      {refLink(r).label}
                    </Link>
                  ))}
                </div>
              )}
              <div className="ask-footer">
                <span>
                  {shortDate(n.created_at)} ·{' '}
                  {n.source_chat ? `from “${n.source_chat}”` : 'written by you'}
                </span>
                <span style={{ flex: 1 }} />
                <button type="button" className="ask-foot-link" onClick={() => onEditNote(n)}>
                  Edit
                </button>
                <button type="button" className="ask-foot-link ask-del" onClick={() => void onDeleteNote(n.id)}>
                  Delete
                </button>
              </div>
            </div>
          ))}
          {notes.length >= noteMax && (
            <div className="ask-keepline">At most {noteMax} notes — delete one first.</div>
          )}
          <div className="ask-keepline">
            Notes are saved by you from an answer, or written here. The agent can read
            them, always with their date, and checks them against today's data. It
            never writes or changes a note.
          </div>
        </>
      )}
    </div>
  );
}

function ThreadRow({
  t,
  onOpen,
  onDelete,
}: {
  t: AskThread;
  onOpen: (id: number) => void;
  onDelete: (id: number) => void;
}) {
  return (
    <div className="ask-thread">
      <button type="button" className="ask-thread-open" onClick={() => onOpen(t.id)}>
        <span className="ask-thread-title">{t.title}</span>
        <span className="ask-thread-meta">
          {t.context?.label ?? 'Whole app'} · {t.questions ?? 0} question
          {(t.questions ?? 0) === 1 ? '' : 's'} · {shortDate(t.updated_at)}
        </span>
      </button>
      <button
        type="button"
        aria-label={`Delete ${t.title}`}
        className="ask-head-btn"
        onClick={() => void onDelete(t.id)}
      >
        ×
      </button>
    </div>
  );
}

function shortTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return iso;
  }
}

function shortDate(iso: string): string {
  try {
    const d = new Date(iso);
    const today = new Date();
    if (d.toDateString() === today.toDateString()) {
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
    return fmtDay(iso);
  } catch {
    return iso;
  }
}
