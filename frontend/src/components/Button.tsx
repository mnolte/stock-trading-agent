import type { ButtonHTMLAttributes } from 'react';

export type ButtonVariant = 'default' | 'primary' | 'danger' | 'ghost';

const backgrounds: Record<ButtonVariant, string> = {
  default: 'var(--sa-raised)',
  primary: 'var(--sa-accent)',
  danger: 'var(--sa-negative)',
  ghost: 'transparent',
};

const colors: Record<ButtonVariant, string> = {
  default: 'var(--sa-text)',
  primary: 'var(--sa-bg)',
  danger: 'var(--sa-bg)',
  ghost: 'var(--sa-text-2)',
};

const borders: Record<ButtonVariant, string> = {
  default: 'var(--sa-border-strong)',
  primary: 'var(--sa-accent)',
  danger: 'var(--sa-negative)',
  ghost: 'transparent',
};

export function Button({ variant = 'default', children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return (
    <button
      {...props}
      style={{
        height: 'var(--sa-control-height)',
        padding: '0 var(--sa-space-4)',
        borderRadius: 'var(--sa-radius-md)',
        background: backgrounds[variant],
        border: `1px solid ${borders[variant]}`,
        color: colors[variant],
        fontSize: 'var(--sa-text-base)',
        fontWeight: 500,
        cursor: 'pointer',
        ...props.style,
      }}
    >
      {children}
    </button>
  );
}
