import { Link } from 'react-router-dom';

import { textInput } from './styles';

// Settings section navigation (item 069): grouped links on wide screens,
// a grouped <select> with "· n changed" on phones. "Find a setting" and
// "Changed from default" stay visible above the select at every width.

export interface NavSection {
  id: string;
  label: string;
  param: string;
}

export interface NavGroup {
  label: string;
  items: NavSection[];
}

export function SectionNav({
  narrow,
  groups,
  currentId,
  onNavigate,
  find,
  onFind,
  changedOnly,
  onChangedOnly,
  sectionChanges,
  activeVersion,
}: {
  narrow: boolean;
  groups: NavGroup[];
  currentId: string;
  onNavigate: (param: string) => void;
  find: string;
  onFind: (value: string) => void;
  changedOnly: boolean;
  onChangedOnly: (value: boolean) => void;
  sectionChanges: (id: string) => number;
  activeVersion: number | null;
}) {
  const paramOf = new Map(groups.flatMap((g) => g.items.map((s) => [s.id, s.param])));
  return (
    <nav
      className="sn"
      aria-label="Settings sections"
      style={{ flex: '1 1 200px', maxWidth: 240, position: 'sticky', top: 24, display: 'flex', flexDirection: 'column', gap: 2 }}
    >
      <input
        className="sn-find"
        type="search"
        aria-label="Find a setting"
        placeholder="Find a setting"
        value={find}
        onChange={(e) => onFind(e.target.value)}
        style={{ ...textInput(), marginBottom: 6, width: '100%' }}
      />
      <label
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 'var(--sa-text-sm)',
          color: 'var(--sa-text-muted)',
          padding: '0 4px 10px',
          cursor: 'pointer',
        }}
      >
        <input
          type="checkbox"
          checked={changedOnly}
          onChange={(e) => onChangedOnly(e.target.checked)}
        />
        Changed from default
      </label>
      {narrow ? (
        <label className="sn-pick" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>Section</span>
          <select
            aria-label="Section"
            value={currentId}
            onChange={(e) => onNavigate(paramOf.get(e.target.value) ?? 'keys')}
            style={{ ...textInput(), width: '100%', marginBottom: 6 }}
          >
            {groups.map((group) => (
              <optgroup key={group.label} label={group.label}>
                {group.items.map((section) => {
                  const count = sectionChanges(section.id);
                  return (
                    <option key={section.id} value={section.id}>
                      {section.label}
                      {count > 0 ? ` · ${count} changed` : ''}
                    </option>
                  );
                })}
              </optgroup>
            ))}
          </select>
        </label>
      ) : (
        groups.map((group) => (
          <div key={group.label} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <span
              className="sn-group"
              style={{
                margin: '12px 0 2px',
                padding: '0 12px',
                fontSize: 'var(--sa-text-xs)',
                fontWeight: 600,
                color: 'var(--sa-text-muted)',
                textTransform: 'uppercase',
                letterSpacing: '0.08em',
              }}
            >
              {group.label}
            </span>
            {group.items.map((section) => {
              const count = sectionChanges(section.id);
              const active = currentId === section.id;
              return (
                <Link
                  key={section.id}
                  to={`/settings/${section.param}`}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 8,
                    minHeight: 36,
                    padding: '0 12px',
                    borderRadius: 'var(--sa-radius-sm)',
                    textDecoration: 'none',
                    fontSize: 'var(--sa-text-base)',
                    background: active ? 'var(--sa-raised)' : 'transparent',
                    color: active ? 'var(--sa-text)' : 'var(--sa-text-2)',
                    boxShadow: active ? 'inset 2px 0 0 var(--sa-accent)' : 'none',
                  }}
                >
                  {section.label}
                  {count > 0 && (
                    <span
                      title={`${count} unsaved change${count === 1 ? '' : 's'}`}
                      style={{
                        minWidth: 20,
                        height: 20,
                        padding: '0 6px',
                        boxSizing: 'border-box',
                        borderRadius: 999,
                        background: 'var(--sa-accent-bg)',
                        border: '1px solid var(--sa-accent-border)',
                        color: 'var(--sa-accent)',
                        fontFamily: 'var(--sa-font-mono)',
                        fontSize: 11,
                        display: 'inline-flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                      }}
                    >
                      {count}
                    </span>
                  )}
                </Link>
              );
            })}
          </div>
        ))
      )}
      <div className="sn-hint" style={{ marginTop: 14, padding: 12, border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-md)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.5 }}>
        Saved settings become a new version and apply from the next cycle. Current:{' '}
        <b style={{ color: 'var(--sa-text-2)', fontWeight: 600 }}>
          {activeVersion === null ? 'no saved version yet' : `v${activeVersion}`}
        </b>
      </div>
    </nav>
  );
}
