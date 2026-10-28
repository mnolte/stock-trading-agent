import { useEffect, useState } from 'react';

import { get, post, errorMessages } from '../api/client';
import { Button } from './Button';

interface ServerStatus {
  version: string;
  image_tag: string;
  deployed_at: string;
  public_url: string;
  instance_id: string;
  started_at: number | null;
  restart_resumed: boolean | null;
  paused: boolean;
  disk: { pct: number; free_gb: number; total_gb: number; level: string } | null;
  clock: { offset_ms: number | null; level: string } | null;
  cert: { expires: string | null; days_left: number | null; level: string } | null;
  backups: {
    local: { latest: string | null; age_hours: number | null; level: string } | null;
    offsite: { at: string; ok: boolean } | null;
    restore_test: { at: string; ok: boolean } | null;
  } | null;
  host: { pending_updates?: number; next_reboot_window?: string } | null;
  heartbeat: { url: string; last_ping: string | null; configured: boolean };
  latest_backup: string | null;
}

const tone = (level?: string | null) =>
  level === 'critical'
    ? 'var(--sa-negative-text)'
    : level === 'warning'
      ? 'var(--sa-warn-text, #FFD58A)'
      : 'var(--sa-positive)';

const icon = (level?: string | null) => (level === 'ok' ? '✓' : level === 'unknown' ? '–' : '⚠');

function Row({
  label,
  help,
  right,
  level,
}: {
  label: string;
  help?: string;
  right: string;
  level?: string | null;
}) {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '22px minmax(0, 1fr) auto',
        gap: 10,
        alignItems: 'start',
        padding: '11px 0',
        borderBottom: '1px solid var(--sa-border)',
        fontSize: 'var(--sa-text-md)',
      }}
    >
      <span style={{ color: tone(level), paddingTop: 2 }} aria-hidden="true">
        {icon(level)}
      </span>
      <span>
        {label}
        {help && (
          <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', marginTop: 2 }}>
            {help}
          </div>
        )}
      </span>
      <span
        style={{
          fontFamily: 'var(--sa-font-mono)',
          fontSize: 'var(--sa-text-sm)',
          color: tone(level),
          textAlign: 'right',
        }}
      >
        {right}
      </span>
    </div>
  );
}

const H3 = ({ children }: { children: string }) => (
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
    {children}
  </h3>
);

const fmtWhen = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString() : 'never';
const fmtStarted = (epoch: number | null) =>
  epoch ? new Date(epoch * 1000).toLocaleString() : '—';

/**
 * Settings → Server (design/screens/Settings.dc.html): health of the
 * server, its backups and outside monitoring. Read-only except the two
 * backup buttons; shown only when the app runs on a VPS
 * (SA_AUTH=required — the API answers 404 otherwise).
 */
export function ServerSection() {
  const [data, setData] = useState<ServerStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [hbUrl, setHbUrl] = useState('');
  const [hbEditing, setHbEditing] = useState(false);
  const [credsOpen, setCredsOpen] = useState(false);
  const [creds, setCreds] = useState({
    endpoint: '',
    bucket: '',
    access_key_id: '',
    secret_access_key: '',
    password: '',
  });

  const refresh = () => {
    get<ServerStatus>('/api/server')
      .then((d) => {
        setData(d);
        setHbUrl(d.heartbeat.url || '');
      })
      .catch((e) => setError(errorMessages(e)[0]));
  };
  useEffect(refresh, []);

  if (error && !data) {
    return (
      <p style={{ margin: '4px 0 14px', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
        {error}
      </p>
    );
  }
  if (!data) return null;

  const backupNow = async () => {
    setBusy(true);
    setError(null);
    try {
      await post('/api/server/backup-now', {});
      setNotice('Backup copy made — see Local copy below.');
      refresh();
    } catch (e) {
      setError(errorMessages(e)[0]);
    } finally {
      setBusy(false);
    }
  };

  const downloadBackup = () => {
    // A GET download behind "Confirm it is you": the client retry path
    // in api/client does not cover navigation, so open it directly — if
    // confirmation is needed the request gets a JSON 403 the user sees.
    setError(null);
    fetch('/api/server/backup-download').then(async (r) => {
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        setError(
          body?.error?.message || body?.detail || `download failed (${r.status})`,
        );
        return;
      }
      const blob = await r.blob();
      const name =
        r.headers.get('content-disposition')?.match(/filename="?([^";]+)/)?.[1] ??
        'stock-agent.db.backup';
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = name;
      a.click();
      URL.revokeObjectURL(url);
    });
  };

  const saveHeartbeat = async () => {
    setError(null);
    try {
      await post('/api/settings', {
        settings: { ops: { heartbeat_url: hbUrl } },
        reason: 'Heartbeat URL',
      });
      setHbEditing(false);
      setNotice('Heartbeat URL saved.');
      refresh();
    } catch (e) {
      setError(errorMessages(e)[0]);
    }
  };

  const saveCreds = async () => {
    setError(null);
    try {
      await post('/api/server/backup-credentials', creds);
      setCredsOpen(false);
      setNotice('Backup credentials stored encrypted.');
      refresh();
    } catch (e) {
      setError(errorMessages(e)[0]);
    }
  };

  const disk = data.disk;
  const clock = data.clock;
  const cert = data.cert;
  const backups = data.backups;

  return (
    <>
      <H3>This server</H3>
      <Row
        label="Version"
        help="Deploys only outside 09:30–16:00 New York; the previous image is kept for rollback."
        right={`v${data.version}${data.image_tag ? ` · image ${data.image_tag}` : ''}${data.deployed_at ? ` · deployed ${fmtWhen(data.deployed_at)}` : ''}`}
      />
      <Row
        label="Address"
        help={`HTTPS certificate renews automatically${cert?.expires ? ` · expires ${cert.expires}` : ''}`}
        right={data.public_url || '—'}
        level={cert?.level}
      />
      <Row
        label="Running since"
        help="After a clean planned restart the agent resumes if it was running before; after an unclean stop it stays paused."
        right={`${fmtStarted(data.started_at)}${data.restart_resumed === true ? ' · clean start, resumed' : data.restart_resumed === false ? ' · stayed paused' : ''}`}
      />
      <Row
        label="Clock"
        help="Cycles depend on the exact time; NTP is checked every hour."
        right={
          clock?.offset_ms != null
            ? `${clock.level === 'ok' ? 'in sync' : 'off'} · offset ${Math.round(clock.offset_ms)} ms`
            : 'offset unknown'
        }
        level={clock?.level}
      />
      <Row
        label="Disk"
        help="Warning at 80%, critical at 90%. Logs, bar store and backups live on the data volume."
        right={
          disk
            ? `${disk.pct}% used · ${disk.free_gb} GB free of ${disk.total_gb} GB`
            : '—'
        }
        level={disk?.level}
      />
      <Row
        label="Operating system updates"
        help="Reboots only in the Sunday window, never during a session."
        right={
          data.host && data.host.pending_updates != null
            ? `${data.host.pending_updates} updates waiting${data.host.next_reboot_window ? ` · next window ${data.host.next_reboot_window}` : ''}`
            : 'security updates automatic'
        }
      />

      <H3>Backups</H3>
      <Row
        label="Local copy"
        right={
          backups?.local?.latest
            ? `${backups.local.latest}${backups.local.age_hours != null ? ` · ${Math.round(backups.local.age_hours)}h ago` : ''}`
            : data.latest_backup ?? 'none yet'
        }
        level={backups?.local?.level}
      />
      <Row
        label="Offsite copy (encrypted)"
        help="Encrypted before upload; the backup password is stored like the API keys."
        right={
          backups?.offsite
            ? `${backups.offsite.ok ? 'ok' : 'failed'} · ${fmtWhen(backups.offsite.at)}`
            : 'not configured'
        }
        level={backups?.offsite ? (backups.offsite.ok ? 'ok' : 'warning') : 'unknown'}
      />
      <Row
        label="Restore test"
        help="Monthly: the latest offsite copy is restored to a temporary folder and checked."
        right={
          backups?.restore_test
            ? `${backups.restore_test.ok ? 'restored and checked' : 'failed'} · ${fmtWhen(backups.restore_test.at)}`
            : 'not run yet'
        }
        level={
          backups?.restore_test ? (backups.restore_test.ok ? 'ok' : 'warning') : 'unknown'
        }
      />

      <H3>Outside monitoring</H3>
      <div style={{ display: 'grid', gridTemplateColumns: '22px minmax(0, 1fr) auto', gap: 10, alignItems: 'start', padding: '11px 0', borderBottom: '1px solid var(--sa-border)', fontSize: 'var(--sa-text-md)' }}>
        <span style={{ color: tone(data.heartbeat.configured ? 'ok' : 'unknown'), paddingTop: 2 }} aria-hidden="true">
          {icon(data.heartbeat.configured ? 'ok' : 'unknown')}
        </span>
        <span>
          Heartbeat
          <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', marginTop: 2 }}>
            An outside service emails you when pings stop, even if this server is down.
          </div>
        </span>
        <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-positive)', textAlign: 'right' }}>
          {data.heartbeat.configured
            ? `last ping ${fmtWhen(data.heartbeat.last_ping)} · after each cycle and check`
            : 'not configured'}
          <Button style={{ marginLeft: 8 }} onClick={() => setHbEditing((v) => !v)}>
            {data.heartbeat.configured ? 'Change…' : 'Set…'}
          </Button>
        </span>
      </div>
      {hbEditing && (
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '8px 0', flexWrap: 'wrap' }}>
          <input
            type="url"
            placeholder="https://hc-ping.com/…"
            aria-label="Heartbeat URL"
            value={hbUrl}
            onChange={(e) => setHbUrl(e.target.value)}
            style={{ flex: '1 1 320px', height: 40, boxSizing: 'border-box', borderRadius: 'var(--sa-radius-md)', border: '1px solid var(--sa-border-strong)', background: 'var(--sa-bg)', color: 'var(--sa-text)', fontSize: 'var(--sa-text-base)', padding: '0 10px', fontFamily: 'var(--sa-font-mono)' }}
          />
          <Button variant="primary" onClick={saveHeartbeat}>Save</Button>
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
            https only, public address — asks “Confirm it is you”.
          </span>
        </div>
      )}
      <Row
        label="Single instance"
        help="Orders carry this instance's id; orders from another id trigger the kill switch."
        right={data.instance_id ? `instance ${data.instance_id}` : '—'}
      />

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, padding: '14px 0 18px' }}>
        <Button onClick={backupNow} disabled={busy}>Back up now</Button>
        <Button  onClick={downloadBackup}>Download latest backup</Button>
        <Button  onClick={() => setCredsOpen((v) => !v)}>Backup credentials…</Button>
        <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', alignSelf: 'center' }}>
          Read-only otherwise: deploys, rollbacks and restores are done on the server with the scripts in docs/DEPLOY.md.
        </span>
      </div>
      {credsOpen && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingBottom: 16 }}>
          {(['endpoint', 'bucket', 'access_key_id', 'secret_access_key', 'password'] as const).map((name) => (
            <input
              key={name}
              type={name.includes('key') || name === 'password' ? 'password' : 'text'}
              placeholder={name.replace(/_/g, ' ')}
              aria-label={name.replace(/_/g, ' ')}
              value={creds[name]}
              onChange={(e) => setCreds({ ...creds, [name]: e.target.value })}
              style={{ height: 40, boxSizing: 'border-box', borderRadius: 'var(--sa-radius-md)', border: '1px solid var(--sa-border-strong)', background: 'var(--sa-bg)', color: 'var(--sa-text)', fontSize: 'var(--sa-text-base)', padding: '0 10px', fontFamily: 'var(--sa-font-mono)' }}
            />
          ))}
          <div>
            <Button variant="primary" onClick={saveCreds}>Save encrypted</Button>
          </div>
        </div>
      )}

      {error && (
        <p role="alert" style={{ color: 'var(--sa-negative-text)', fontSize: 'var(--sa-text-sm)' }}>{error}</p>
      )}
      {notice && (
        <p role="status" style={{ color: 'var(--sa-positive)', fontSize: 'var(--sa-text-sm)' }}>{notice}</p>
      )}
    </>
  );
}
