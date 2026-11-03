import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { refLink } from './askFormat';

interface Unverified {
  text: string;
  start: number;
  end: number;
}

/** Strict Markdown: paragraphs, lists, **bold**, `code`, small tables,
 * [[ref]] chips. No raw HTML, no images, no external links. Unverified
 * numbers get the dotted amber underline. */
export function AskMarkdown({
  text,
  unverified = [],
  onNoteRef,
}: {
  text: string;
  unverified?: Unverified[];
  onNoteRef?: (ref: string) => void;
}) {
  return (
    <div className="ask-md">
      {renderBlocks(text, unverified, onNoteRef)}
    </div>
  );
}

function renderBlocks(
  text: string,
  unverified: Unverified[],
  onNoteRef?: (ref: string) => void,
): ReactNode[] {
  const blocks: ReactNode[] = [];
  const lines = text.split('\n');
  let key = 0;
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    // Table: consecutive | ... | lines with a --- separator row.
    if (line.trim().startsWith('|') && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1]) && lines[i + 1].includes('-')) {
      const header = splitRow(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        rows.push(splitRow(lines[i]));
        i += 1;
      }
      blocks.push(
        <div key={key++} className="ask-md-table-wrap">
          <table className="ask-md-table">
            <thead>
              <tr>{header.map((c, j) => <th key={j}>{inline(c, unverified, onNoteRef)}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, j) => (
                <tr key={j}>{r.map((c, k) => <td key={k}>{inline(c, unverified, onNoteRef)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }
    // List.
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ''));
        i += 1;
      }
      blocks.push(
        <ul key={key++} className="ask-md-list">
          {items.map((item, j) => (
            <li key={j}>{inline(item, unverified, onNoteRef)}</li>
          ))}
        </ul>,
      );
      continue;
    }
    // Blank line.
    if (!line.trim()) {
      i += 1;
      continue;
    }
    // Paragraph: gather until blank/list/table.
    const para: string[] = [line];
    i += 1;
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^\s*[-*]\s+/.test(lines[i]) &&
      !lines[i].trim().startsWith('|')
    ) {
      para.push(lines[i]);
      i += 1;
    }
    blocks.push(<p key={key++}>{inline(para.join(' '), unverified, onNoteRef)}</p>);
  }
  return blocks;
}

function splitRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\||\|$/g, '')
    .split('|')
    .map((c) => c.trim());
}

const REF_RE = /\[\[([a-z_]+:[^\]]+?)\]\]/g;
const BOLD_RE = /\*\*([^*]+)\*\*/g;
const CODE_RE = /`([^`]+)`/g;

function inline(
  text: string,
  unverified: Unverified[],
  onNoteRef?: (ref: string) => void,
  // global offset of this text inside the message — unused here since we
  // mark by value: the backend stores positions, we match by raw text.
): ReactNode[] {
  const parts: ReactNode[] = [];
  let key = 0;
  // Tokenize by refs, bold, code; then number underline inside plain runs.
  const combined = new RegExp(
    `${REF_RE.source}|${BOLD_RE.source}|${CODE_RE.source}`,
    'g',
  );
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = combined.exec(text)) !== null) {
    if (m.index > last) parts.push(...markNumbers(text.slice(last, m.index), unverified, key));
    key = parts.length;
    if (m[1]) {
      const ref = m[1];
      const link = refLink(ref);
      if (ref.startsWith('note:')) {
        parts.push(
          <button key={`r${key++}`} type="button" className="ask-ref" onClick={() => onNoteRef?.(ref)}>
            {link.label}
          </button>,
        );
      } else {
        parts.push(
          <Link key={`r${key++}`} to={link.href} className="ask-ref">
            {link.label}
          </Link>,
        );
      }
    } else if (m[2]) {
      parts.push(<strong key={`b${key++}`}>{m[2]}</strong>);
    } else if (m[3]) {
      parts.push(<code key={`c${key++}`}>{m[3]}</code>);
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(...markNumbers(text.slice(last), unverified, key));
  return parts;
}

/** Wrap numbers flagged unverified in the amber dotted underline. */
function markNumbers(
  text: string,
  unverified: Unverified[],
  startKey: number,
): ReactNode[] {
  if (!unverified.length || !text) return [text];
  const tokens: ReactNode[] = [];
  let key = startKey * 1000;
  let rest = text;
  let guard = 0;
  while (rest && guard++ < 100) {
    let earliest = -1;
    let needle = '';
    for (const u of unverified) {
      const idx = rest.indexOf(u.text);
      if (idx >= 0 && (earliest < 0 || idx < earliest)) {
        earliest = idx;
        needle = u.text;
      }
    }
    if (earliest < 0) break;
    tokens.push(rest.slice(0, earliest));
    tokens.push(
      <span
        key={`u${key++}`}
        className="ask-unverified"
        title="This number was not found in the data the agent looked up"
      >
        {needle}
      </span>,
    );
    rest = rest.slice(earliest + needle.length);
  }
  if (rest) tokens.push(rest);
  return tokens;
}
