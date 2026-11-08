import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ButtonHTMLAttributes, CSSProperties, ReactNode } from 'react';

import {
  ACTION_TIMEOUT_MS,
  BUTTON_WAIT_MS,
} from '../api/timing';

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

type BusyPhase = 'working' | 'waiting' | 'noanswer';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  /** The action is running: spinner + `busyLabel`, the idle width is kept,
   * aria-busy + aria-disabled, clicks ignored — never the disabled
   * attribute, so focus stays on the button (design/DESIGN.md → Loading). */
  busy?: boolean;
  /** Present-participle label while busy ("Saving…"); defaults to the
   * children plus an ellipsis. */
  busyLabel?: ReactNode;
  /** What the button is waiting for, shown muted after 10 s
   * ("Still saving", "Alpaca is slow"). */
  waitingFor?: string;
}

export function Button({
  variant = 'default',
  busy = false,
  busyLabel,
  waitingFor,
  children,
  onClick,
  style,
  ...props
}: ButtonProps) {
  const [phase, setPhase] = useState<BusyPhase>('working');
  const [elapsed, setElapsed] = useState(0);
  const btnRef = useRef<HTMLButtonElement | null>(null);
  const idleWidth = useRef<number | null>(null);

  // Measure the idle width so the busy label never shrinks the button.
  useLayoutEffect(() => {
    if (!busy && btnRef.current) {
      idleWidth.current = btnRef.current.offsetWidth;
    }
  }, [busy, children]);

  useEffect(() => {
    if (!busy) {
      setPhase('working');
      setElapsed(0);
      return;
    }
    const start = Date.now();
    const tick = window.setInterval(() => {
      const seconds = Math.floor((Date.now() - start) / 1000);
      setElapsed(seconds);
      setPhase(
        seconds * 1000 >= ACTION_TIMEOUT_MS
          ? 'noanswer'
          : seconds * 1000 >= BUTTON_WAIT_MS
            ? 'waiting'
            : 'working',
      );
    }, 500);
    return () => window.clearInterval(tick);
  }, [busy]);

  const working = busy && phase !== 'noanswer';
  const button = (
    <button
      {...props}
      ref={btnRef}
      type={props.type ?? 'button'}
      aria-busy={working || undefined}
      aria-disabled={working || undefined}
      onClick={working ? undefined : onClick}
      style={{
        height: 'var(--sa-control-height)',
        padding: '0 var(--sa-space-4)',
        borderRadius: 'var(--sa-radius-md)',
        background: backgrounds[variant],
        border: `1px solid ${borders[variant]}`,
        color: colors[variant],
        fontSize: 'var(--sa-text-base)',
        fontWeight: 500,
        cursor: working ? 'progress' : 'pointer',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 'var(--sa-space-2)',
        whiteSpace: 'nowrap',
        minWidth: busy && idleWidth.current ? idleWidth.current : undefined,
        ...style,
      }}
    >
      {working ? (
        <>
          <span className="sa-spin" aria-hidden="true" />
          <span>{busyLabel ?? <>{children}…</>}</span>
        </>
      ) : (
        children
      )}
    </button>
  );

  if (!busy || (!waitingFor && phase === 'working')) return button;
  const line: CSSProperties = {
    fontSize: 'var(--sa-text-sm)',
    lineHeight: 1.45,
    color:
      phase === 'noanswer' ? 'var(--sa-warning-text)' : 'var(--sa-text-muted)',
  };
  return (
    <span
      style={{
        display: 'inline-flex',
        flexDirection: 'column',
        alignItems: 'flex-start',
        gap: 4,
      }}
    >
      {button}
      {phase === 'waiting' && waitingFor && (
        <span style={line}>
          {waitingFor} · {elapsed} s
        </span>
      )}
      {phase === 'noanswer' && (
        <span style={line}>No answer · {elapsed} s</span>
      )}
    </span>
  );
}
