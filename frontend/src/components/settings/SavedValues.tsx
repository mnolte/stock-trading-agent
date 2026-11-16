import type { SavedWithoutControl } from '../../types/api';
import { fmtDay } from '../../format';
import { Button } from '../Button';

export function SavedValues({ items, onUse }: {
  items: SavedWithoutControl[];
  onUse: (item: SavedWithoutControl) => void;
}) {
  if (!items.length) return null;
  const value = (v: unknown) => typeof v === 'string' ? v : JSON.stringify(v);
  return (
    <div role="region" aria-label="Saved values without a control" style={{ margin: '8px 0 6px', padding: '14px 16px', border: '1px solid var(--sa-warning-border)', background: 'var(--sa-warning-bg)', borderRadius: 'var(--sa-radius-md)', display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', justifyContent: 'space-between', gap: '8px 16px' }}>
        <h3 style={{ margin: 0, fontSize: 'var(--sa-text-base)', color: 'var(--sa-warning-text)' }}>{items.length} saved {items.length === 1 ? 'value differs' : 'values differ'} from the config and {items.length === 1 ? 'has' : 'have'} no control</h3>
        <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>checked at every start and every save</span>
      </div>
      <p style={{ margin: 0, fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.5, maxWidth: 760 }}>A saved version stores every setting, also the ones this screen can’t change. The saved value wins over the config file, so a changed default doesn’t reach the agent until you take it over here.</p>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', minWidth: 620, borderCollapse: 'collapse' }}>
          <thead><tr>{['Setting', 'Saved (in use)', 'Config file', 'Saved since', 'Action'].map((h) => <th key={h} scope="col" style={{ textAlign: 'left', padding: '8px 10px', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', borderBottom: '1px solid var(--sa-border)', whiteSpace: 'nowrap' }}>{h}</th>)}</tr></thead>
          <tbody>{items.map((item) => <tr key={item.key}>
            <td style={{ padding: 10, borderBottom: '1px solid var(--sa-border)' }}>{item.label}<div style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{item.key}</div></td>
            <td style={{ padding: 10, borderBottom: '1px solid var(--sa-border)', fontFamily: 'var(--sa-font-mono)' }}>{value(item.saved)}</td>
            <td style={{ padding: 10, borderBottom: '1px solid var(--sa-border)', fontFamily: 'var(--sa-font-mono)' }}>{value(item.config)}</td>
            <td style={{ padding: 10, borderBottom: '1px solid var(--sa-border)', whiteSpace: 'nowrap', color: 'var(--sa-text-muted)' }}>v{item.since_version} · {fmtDay(item.since_at)}</td>
            <td style={{ padding: 10, borderBottom: '1px solid var(--sa-border)', textAlign: 'right' }}><Button onClick={() => onUse(item)}>Use the config value</Button></td>
          </tr>)}</tbody>
        </table>
      </div>
      <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.5 }}>Use the config value saves a new version with a reason, like any change; a looser value asks you to confirm. With none: “Every saved value has a control or equals the config file.”</div>
    </div>
  );
}
