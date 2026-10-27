import { useEffect, useState } from 'react';

import { get, post, errorMessages } from '../api/client';
import type { AuthSessions, AuthStatus } from '../api/auth';
import { Button } from './Button';

const inputStyle: React.CSSProperties = {
  height: 40,
  boxSizing: 'border-box',
  borderRadius: 'var(--sa-radius-md)',
  border: '1px solid var(--sa-border-strong)',
  background: 'var(--sa-bg)',
  color: 'var(--sa-text)',
  fontSize: 'var(--sa-text-base)',
  padding: '0 10px',
};

/**
 * Settings → Access (design/screens/Settings.dc.html): sign-in state,
 * password and authenticator management, recovery codes, idle hours,
 * signed-in devices and the failed-sign-ins line. With SA_AUTH=off the
 * whole panel collapses to "Sign-in off (local only)".
 */
export function AccessSection({ auth }: { auth: AuthStatus }) {
  const [sessions, setSessions] = useState<AuthSessions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // password change
  const [pwOpen, setPwOpen] = useState(false);
  const [currentPw, setCurrentPw] = useState('');
  const [newPw, setNewPw] = useState('');
  // authenticator reset result (shown once)
  const [totpUri, setTotpUri] = useState<string | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);

  const refresh = () => {
    if (!auth.required) return;
    get<AuthSessions>('/api/auth/sessions')
      .then(setSessions)
      .catch((e) => setError(errorMessages(e)[0]));
  };
  useEffect(refresh, [auth.required]);

  if (!auth.required) {
    return (
      <p style={{ margin: '4px 0 14px', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
        Sign-in off (local only). On a server the app refuses to start without it.
      </p>
    );
  }

  const fail =
    sessions?.failed && sessions.failed.count > 0
      ? `Failed sign-ins in the last 7 days: ${sessions.failed.count} from ${sessions.failed.addresses} address${sessions.failed.addresses === 1 ? '' : 'es'}` +
        (sessions.failed.last_at
          ? ` · last ${new Date(sessions.failed.last_at).toLocaleString()} from ${sessions.failed.last_ip ?? 'unknown'}`
          : '')
      : 'No failed sign-ins in the last 7 days.';

  const changePassword = async () => {
    setError(null);
    try {
      await post('/api/auth/password', {
        current_password: currentPw,
        new_password: newPw,
      });
      setNotice('Password changed — other devices were signed out.');
      setPwOpen(false);
      setCurrentPw('');
      setNewPw('');
    } catch (e) {
      setError(errorMessages(e)[0]);
    }
  };

  const resetAuthenticator = async () => {
    setError(null);
    try {
      const r = await post<{ otpauth_uri: string; secret: string }>(
        '/api/auth/totp',
        {},
      );
      setTotpUri(r.otpauth_uri);
      refresh();
    } catch (e) {
      setError(errorMessages(e)[0]);
    }
  };

  const newCodes = async () => {
    setError(null);
    try {
      const r = await post<{ codes: string[] }>('/api/auth/recovery-codes', {});
      setCodes(r.codes);
      refresh();
    } catch (e) {
      setError(errorMessages(e)[0]);
    }
  };

  const signOutDevice = async (id: number) => {
    try {
      await post(`/api/auth/sessions/${id}/sign-out`, {});
      refresh();
    } catch (e) {
      setError(errorMessages(e)[0]);
    }
  };

  const signOutOthers = async () => {
    try {
      await post('/api/auth/sign-out-others', {});
      setNotice('Every other device was signed out.');
      refresh();
    } catch (e) {
      setError(errorMessages(e)[0]);
    }
  };

  const row: React.CSSProperties = {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: '10px 20px',
    padding: '12px 0',
    borderBottom: '1px solid var(--sa-border)',
  };
  const rowLabel: React.CSSProperties = {
    flex: '1 1 300px',
    minWidth: 0,
    display: 'flex',
    flexDirection: 'column',
    gap: 3,
  };

  return (
    <>
      <div style={row}>
        <div style={rowLabel}>
          <span style={{ fontSize: 'var(--sa-text-base)' }}>Sign-in</span>
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.45 }}>
            The app refuses to start without sign-in on any address other than 127.0.0.1.
          </span>
        </div>
        <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-positive)' }}>
          required · password + authenticator
        </span>
      </div>

      <div style={row}>
        <div style={rowLabel}>
          <span style={{ fontSize: 'var(--sa-text-base)' }}>Password</span>
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
            At least 14 characters; stored as an Argon2id hash.
          </span>
        </div>
        <Button onClick={() => setPwOpen((v) => !v)}>Change…</Button>
      </div>
      {pwOpen && (
        <div style={{ ...row, borderBottom: 'none', gap: 10, alignItems: 'flex-end' }}>
          <input
            type="password"
            autoComplete="current-password"
            placeholder="Current password"
            aria-label="Current password"
            value={currentPw}
            onChange={(e) => setCurrentPw(e.target.value)}
            style={{ ...inputStyle, flex: '0 0 180px' }}
          />
          <input
            type="password"
            autoComplete="new-password"
            placeholder="New password (14+ characters)"
            aria-label="New password"
            value={newPw}
            onChange={(e) => setNewPw(e.target.value)}
            style={{ ...inputStyle, flex: '0 0 220px' }}
          />
          <Button variant="primary" onClick={changePassword} disabled={!currentPw || !newPw}>
            Save
          </Button>
        </div>
      )}

      <div style={row}>
        <div style={rowLabel}>
          <span style={{ fontSize: 'var(--sa-text-base)' }}>Authenticator</span>
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
            Codes from any TOTP app. Recovery codes are shown once, when created.
          </span>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
          <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-positive)' }}>
            on · {sessions?.recovery_codes_left ?? '…'} of 10 recovery codes left
          </span>
          <div style={{ display: 'flex', gap: 8 }}>
            <Button onClick={resetAuthenticator}>New authenticator…</Button>
            <Button onClick={newCodes}>New recovery codes…</Button>
          </div>
        </div>
      </div>
      {totpUri && (
        <div style={{ padding: '8px 0', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-2)' }}>
          Add this URI to your authenticator app now — it is not shown again:{' '}
          <code style={{ fontFamily: 'var(--sa-font-mono)', wordBreak: 'break-all' }}>{totpUri}</code>
        </div>
      )}
      {codes && (
        <div style={{ padding: '8px 0' }}>
          <p style={{ margin: '0 0 6px', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-2)' }}>
            New recovery codes — stored hashed; they are never shown again:
          </p>
          <div style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text)', columnCount: 2, columnGap: 24 }}>
            {codes.map((c) => (
              <div key={c}>{c}</div>
            ))}
          </div>
        </div>
      )}

      <h3
        style={{
          margin: '18px 0 0',
          fontSize: 'var(--sa-text-xs)',
          fontWeight: 600,
          color: 'var(--sa-text-muted)',
          textTransform: 'uppercase',
          letterSpacing: 'var(--sa-label-tracking)',
        }}
      >
        Signed-in devices
      </h3>
      <div style={{ padding: '4px 0 6px' }}>
        {(sessions?.devices ?? []).map((d) => (
          <div
            key={d.id}
            style={{
              display: 'grid',
              gridTemplateColumns: 'minmax(0,1.3fr) minmax(0,1.3fr) 110px auto',
              gap: 10,
              alignItems: 'center',
              padding: '10px 0',
              borderBottom: '1px solid var(--sa-border)',
              fontSize: 'var(--sa-text-md)',
            }}
          >
            <span>
              <b style={{ fontWeight: 600 }}>{d.current ? 'This device' : d.device}</b>
              <br />
              <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{d.device}</span>
            </span>
            <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-2)' }}>
              {d.ip}
            </span>
            <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
              {d.current ? 'now' : new Date(d.last_seen_at).toLocaleString()}
            </span>
            <span style={{ textAlign: 'right' }}>
              {d.current ? (
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    height: 24,
                    padding: '0 8px',
                    borderRadius: 6,
                    fontFamily: 'var(--sa-font-mono)',
                    fontSize: 'var(--sa-text-sm)',
                    background: 'var(--sa-raised)',
                    color: 'var(--sa-positive)',
                    border: '1px solid var(--sa-border)',
                  }}
                >
                  this device
                </span>
              ) : (
                <Button onClick={() => signOutDevice(d.id)}>Sign out</Button>
              )}
            </span>
          </div>
        ))}
        {sessions && sessions.devices.length === 0 && (
          <p style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}>
            No active sessions besides this one.
          </p>
        )}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, paddingTop: 12, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{fail}</span>
          <Button onClick={signOutOthers}>Sign out everywhere else</Button>
        </div>
      </div>

      {error && (
        <p role="alert" style={{ color: 'var(--sa-negative-text)', fontSize: 'var(--sa-text-sm)' }}>{error}</p>
      )}
      {notice && (
        <p role="status" style={{ color: 'var(--sa-positive)', fontSize: 'var(--sa-text-sm)' }}>{notice}</p>
      )}
    </>
  );
}
