import { useMemo, useState } from 'react';

/**
 * A review story (item 079, docs/SPEC.md — Reviews → Stories): one to
 * three plain paragraphs. Glossary terms from the stored facts render
 * as underlined buttons — hover shows the definition on desktop, a tap
 * opens a small popover on phones (design screens/Reviews.dc.html,
 * `.sa-term`). Only the first occurrence of each term per paragraph is
 * annotated.
 */

interface Term {
  term: string;
  definition: string;
}

interface Match {
  start: number;
  end: number;
  term: Term;
}

function termPattern(term: string): RegExp {
  return new RegExp(`(?<![A-Za-z])${term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![A-Za-z])`, 'gi');
}

/** First occurrence per term, non-overlapping, earliest first. */
function paragraphMatches(text: string, terms: Term[]): Match[] {
  const found: Match[] = [];
  for (const term of terms) {
    const m = termPattern(term.term).exec(text);
    if (m) {
      found.push({ start: m.index, end: m.index + m[0].length, term });
    }
  }
  found.sort((a, b) => a.start - b.start || b.end - a.end);
  const kept: Match[] = [];
  let covered = -1;
  for (const m of found) {
    if (m.start < covered) continue;
    kept.push(m);
    covered = m.end;
  }
  return kept;
}

function TermButton({
  term,
  open,
  onToggle,
}: {
  term: Term;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <span style={{ position: 'relative', display: 'inline' }}>
      <button
        type="button"
        className="sa-term"
        title={term.definition}
        aria-label={`${term.term}: ${term.definition}`}
        aria-expanded={open}
        onClick={onToggle}
        style={{
          display: 'inline',
          margin: 0,
          padding: 0,
          border: 0,
          background: 'none',
          font: 'inherit',
          lineHeight: 'inherit',
          cursor: 'help',
          color: 'inherit',
          textDecoration: 'underline dotted',
          textDecorationColor: 'var(--sa-text-muted)',
          textUnderlineOffset: '3px',
        }}
      >
        {term.term}
      </button>
      {open && (
        <span
          role="tooltip"
          style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            zIndex: 30,
            marginTop: '4px',
            width: 'max-content',
            maxWidth: '280px',
            padding: 'var(--sa-space-2) var(--sa-space-3)',
            border: '1px solid var(--sa-border-strong)',
            borderRadius: 'var(--sa-radius-md)',
            background: 'var(--sa-raised)',
            boxShadow: '0 8px 24px rgb(0 0 0 / 0.35)',
            fontSize: 'var(--sa-text-sm)',
            lineHeight: 1.5,
            color: 'var(--sa-text-2)',
            textAlign: 'left',
          }}
        >
          {term.definition}
        </span>
      )}
    </span>
  );
}

export function StoryText({
  text,
  glossary,
}: {
  text: string;
  glossary?: Record<string, string> | null;
}) {
  const [openKey, setOpenKey] = useState<string | null>(null);
  const terms = useMemo<Term[]>(
    () =>
      Object.entries(glossary ?? {})
        .filter(([, def]) => typeof def === 'string' && def)
        .map(([term, definition]) => ({ term, definition })),
    [glossary],
  );
  const paragraphs = useMemo(
    () => text.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean),
    [text],
  );
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '12px',
        maxWidth: '780px',
        lineHeight: 1.65,
        color: 'var(--sa-text-2)',
      }}
    >
      {paragraphs.map((para, pi) => {
        const matches = paragraphMatches(para, terms);
        const nodes: React.ReactNode[] = [];
        let cursor = 0;
        matches.forEach((m, mi) => {
          if (m.start > cursor) nodes.push(para.slice(cursor, m.start));
          const key = `${pi}:${mi}:${m.term.term}`;
          nodes.push(
            <TermButton
              key={key}
              term={m.term}
              open={openKey === key}
              onToggle={() => setOpenKey(openKey === key ? null : key)}
            />,
          );
          cursor = m.end;
        });
        if (cursor < para.length) nodes.push(para.slice(cursor));
        return (
          <p key={pi} style={{ margin: 0 }}>
            {nodes}
          </p>
        );
      })}
    </div>
  );
}
