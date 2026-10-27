import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { post, errorMessages } from '../api/client';

/**
 * Sign in (design/screens/Login.dc.html): password → authenticator or
 * recovery code → done. The locked state is the 429 answer rendered
 * in place. "Stock Trading Agent" is the product name.
 */
export default function Login() {
  const [params] = useSearchParams();
  const next = params.get('next') || '/';
  const expired = params.get('expired') === '1';

  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [code, setCode] = useState('');
  const [useRecovery, setUseRecovery] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const card: React.CSSProperties = {
    width: 380,
    maxWidth: '100%',
    boxSizing: 'border-box',
    background: 'var(--sa-panel)',
    border: '1px solid var(--sa-border)',
    borderRadius: 'var(--sa-radius-lg)',
    padding: '24px 28px',
    display: 'flex',
    flexDirection: 'column',
    gap: 14,
    boxShadow: '0 20px 40px rgba(0,0,0,0.45)',
  };
  const input: React.CSSProperties = {
    width: '100%',
    height: 40,
    boxSizing: 'border-box',
    borderRadius: 'var(--sa-radius-md)',
    border: '1px solid var(--sa-border)',
    background: 'var(--sa-bg)',
    color: 'var(--sa-text)',
    padding: '0 10px',
    fontSize: 'var(--sa-text-base)',
  };
  const label: React.CSSProperties = {
    fontSize: 'var(--sa-text-md)',
    color: 'var(--sa-text-2)',
    display: 'block',
    marginBottom: 6,
  };
  const button: React.CSSProperties = {
    height: 40,
    borderRadius: 'var(--sa-radius-md)',
    border: 'none',
    background: 'var(--sa-accent)',
    color: '#0B0E13',
    fontWeight: 600,
    fontSize: 'var(--sa-text-base)',
    cursor: 'pointer',
  };
  const link: React.CSSProperties = {
    background: 'none',
    border: 'none',
    padding: 0,
    color: 'var(--sa-accent)',
    fontSize: 'var(--sa-text-md)',
    cursor: 'pointer',
    textAlign: 'left',
  };

  const submitPassword = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await post<{ pending: string }>('/api/auth/sign-in', {
        password,
        remember,
      });
      setPending(r.pending);
    } catch (e) {
      setError(errorMessages(e)[0]);
    } finally {
      setBusy(false);
    }
  };

  const submitCode = async () => {
    setBusy(true);
    setError(null);
    try {
      await post('/api/auth/code', { pending, code });
      window.location.assign(next);
    } catch (e) {
      setError(errorMessages(e)[0]);
      setBusy(false);
    }
  };

  const chrome = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <div
        aria-hidden
        style={{
          width: 36,
          height: 36,
          borderRadius: 'var(--sa-radius-md)',
          background: 'var(--sa-accent)',
          color: '#0B0E13',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontWeight: 700,
          fontSize: 14,
        }}
      >
        SA
      </div>
      <div>
        <div style={{ fontWeight: 600, fontSize: 'var(--sa-text-lg)' }}>Stock Trading Agent</div>
        <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          {window.location.host} · PAPER
        </div>
      </div>
    </div>
  );

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 16,
        padding: 24,
      }}
    >
      {chrome}
      <div style={card}>
        {expired && (
          <div
            role="status"
            style={{
              padding: '10px 12px',
              borderRadius: 'var(--sa-radius-md)',
              background: 'var(--sa-raised)',
              border: '1px solid var(--sa-border)',
              color: 'var(--sa-text-2)',
              fontSize: 'var(--sa-text-md)',
              lineHeight: 1.5,
            }}
          >
            You were signed out after a while without activity. The agent kept running.
          </div>
        )}
        {pending === null ? (
          <>
            <h1 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>Sign in</h1>
            <div>
              <label htmlFor="pw" style={label}>
                Password
              </label>
              <input
                id="pw"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && submitPassword()}
                style={input}
              />
            </div>
            <label
              style={{
                display: 'flex',
                gap: 10,
                alignItems: 'center',
                fontSize: 'var(--sa-text-md)',
                color: 'var(--sa-text-2)',
              }}
            >
              <input
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
              />
              Keep me signed in on this device for 30 days
            </label>
            {error && (
              <p role="alert" style={{ margin: 0, fontSize: 'var(--sa-text-md)', color: 'var(--sa-negative)' }}>
                {error}
              </p>
            )}
            <button type="button" style={button} onClick={submitPassword} disabled={busy || !password}>
              Continue
            </button>
            <p style={{ margin: 0, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.5 }}>
              One account. It is created on the server with{' '}
              <code>make create-user</code>; there is no sign-up page.
            </p>
          </>
        ) : (
          <>
            <h1 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>Authenticator code</h1>
            <p style={{ margin: 0, fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.5 }}>
              {useRecovery
                ? 'Enter one of your recovery codes — each works once.'
                : 'Enter the 6-digit code from your authenticator app.'}
            </p>
            <div>
              <label htmlFor="otp" style={label}>
                {useRecovery ? 'Recovery code' : 'Code'}
              </label>
              <input
                id="otp"
                inputMode={useRecovery ? 'text' : 'numeric'}
                autoComplete="one-time-code"
                autoFocus
                value={code}
                onChange={(e) => setCode(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && submitCode()}
                placeholder={useRecovery ? 'XXXX-XXXX-XXXX' : '000 000'}
                style={{ ...input, fontFamily: 'var(--sa-mono)' }}
              />
            </div>
            {error && (
              <p role="alert" style={{ margin: 0, fontSize: 'var(--sa-text-md)', color: 'var(--sa-negative)' }}>
                {error}
              </p>
            )}
            <button type="button" style={button} onClick={submitCode} disabled={busy || !code}>
              Sign in
            </button>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <button type="button" style={link} onClick={() => setUseRecovery(!useRecovery)}>
                {useRecovery ? 'Use the authenticator code' : 'Use a recovery code'}
              </button>
              <button
                type="button"
                style={link}
                onClick={() => {
                  setPending(null);
                  setCode('');
                  setError(null);
                }}
              >
                Back
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
