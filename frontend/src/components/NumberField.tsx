import type { InputHTMLAttributes } from 'react';

export function NumberField({
  label,
  unit,
  help,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; unit?: string; help?: string }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
      <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{label}</span>
      <div style={{ position: 'relative' }}>
        <input
          type="number"
          {...props}
          style={{
            width: '100%',
            height: 'var(--sa-input-height)',
            padding: `0 ${unit ? '40px' : '12px'} 0 12px`,
            borderRadius: 'var(--sa-radius-md)',
            background: 'var(--sa-raised)',
            border: '1px solid var(--sa-border)',
            color: 'var(--sa-text)',
            fontFamily: 'var(--sa-font-mono)',
            fontSize: 'var(--sa-text-base)',
            ...props.style,
          }}
        />
        {unit && (
          <span
            style={{
              position: 'absolute',
              right: '12px',
              top: 0,
              height: '100%',
              display: 'flex',
              alignItems: 'center',
              color: 'var(--sa-text-muted)',
              fontSize: 'var(--sa-text-sm)',
              pointerEvents: 'none',
            }}
          >
            {unit}
          </span>
        )}
      </div>
      {help && <span style={{ fontSize: 'var(--sa-text-xs)', color: 'var(--sa-text-muted)' }}>{help}</span>}
    </label>
  );
}
