import { useCallback, useEffect, useMemo, useState } from 'react';

import { errorMessages } from '../api/client';
import { useLiveUpdates } from '../api/liveContext';
import {
  getSettings,
  postPaperReset,
  postSettings,
  postStartRun,
} from '../api/state';
import type { SettingsForm, SettingsResponse } from '../types/api';
import { Banner } from '../components/Banner';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { NumberField } from '../components/NumberField';
import { ResetAccountDialog, SaveSettingsDialog } from '../components/SettingsDialogs';
import { Toggle } from '../components/Toggle';
import { displayDateTime } from '../format';
import { diffSettings } from './settingsDiff';

function Grid({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))',
        gap: 'var(--sa-space-4)',
      }}
    >
      {children}
    </div>
  );
}

export default function Settings() {
  const [data, setData] = useState<SettingsResponse | null>(null);
  const [form, setForm] = useState<SettingsForm | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [newSymbol, setNewSymbol] = useState('');
  const [newSector, setNewSector] = useState('');
  const [resetOpen, setResetOpen] = useState(false);
  const [resetStep2, setResetStep2] = useState<string | null>(null);
  const [resetBusy, setResetBusy] = useState(false);

  const refresh = useCallback(() => {
    getSettings()
      .then((d) => {
        setData(d);
        setForm((f) => f ?? structuredClone(d.settings));
      })
      .catch(console.error);
  }, []);

  useEffect(refresh, [refresh]);
  useLiveUpdates(['header'], refresh);

  const diffs = useMemo(
    () => (data && form ? diffSettings(data.settings, form) : []),
    [data, form],
  );
  const dirty = diffs.length > 0;

  const patch = (section: keyof SettingsForm, key: string, value: unknown) => {
    setForm((f) =>
      f ? { ...f, [section]: { ...(f[section] as object), [key]: value } } : f,
    );
    setNotice(null);
  };

  const addSymbol = () => {
    if (!form || !newSymbol.trim() || !newSector) return;
    const symbol = newSymbol.trim().toUpperCase();
    patch('universe', 'symbols', [...form.universe.symbols, symbol]);
    patch('universe', 'sectors', { ...form.universe.sectors, [symbol]: newSector });
    setNewSymbol('');
    setNewSector('');
  };

  const removeSymbol = (symbol: string) => {
    if (!form) return;
    patch(
      'universe',
      'symbols',
      form.universe.symbols.filter((s) => s !== symbol),
    );
    const sectors = { ...form.universe.sectors };
    delete sectors[symbol];
    patch('universe', 'sectors', sectors);
  };

  const save = (reason: string) => {
    if (!form) return;
    setSaving(true);
    setErrors([]);
    postSettings(form, reason)
      .then((res) => {
        setSaveOpen(false);
        setForm(null);
        setNotice(`Saved as v${res.version} · applies at the next cycle.`);
        refresh();
      })
      .catch((e) => setErrors(errorMessages(e)))
      .finally(() => setSaving(false));
  };

  const doReset = () => {
    setResetBusy(true);
    setErrors([]);
    postPaperReset()
      .then((res) => {
        setResetOpen(false);
        setResetStep2(res.message);
      })
      .catch((e) => setErrors(errorMessages(e)))
      .finally(() => setResetBusy(false));
  };

  const startRun = () => {
    setResetBusy(true);
    postStartRun()
      .then((res) => {
        setResetStep2(null);
        setNotice(res.message ?? 'New run started.');
        refresh();
      })
      .catch((e) => setErrors(errorMessages(e)))
      .finally(() => setResetBusy(false));
  };

  if (!data || !form) return null;
  const s = form;
  const scheduleRows = data.schedule.rows;
  const held = new Set(data.held_symbols);

  return (
    <div style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}>
      <h2 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>Settings</h2>

      {errors.length > 0 && (
        <Banner variant="negative">{errors.join(' ')}</Banner>
      )}
      {notice && <Banner variant="info">{notice}</Banner>}
      {data.pending_version !== null && (
        <Banner variant="info">
          v{data.pending_version} is pending — it applies at the next cycle.
        </Banner>
      )}

      <Card label="Execution">
        <div style={{ display: 'inline-flex', background: 'var(--sa-raised)', border: '1px solid var(--sa-border)', borderRadius: 'var(--sa-radius-md)', padding: '2px' }}>
          {['automatic', 'supervised'].map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => patch('execution', 'mode', mode)}
              style={{
                height: '32px',
                padding: '0 var(--sa-space-4)',
                borderRadius: 'var(--sa-radius-sm)',
                background: s.execution.mode === mode ? 'var(--sa-panel)' : 'transparent',
                border: 'none',
                color: s.execution.mode === mode ? 'var(--sa-text)' : 'var(--sa-text-muted)',
                fontSize: 'var(--sa-text-sm)',
                fontWeight: 500,
                cursor: 'pointer',
                textTransform: 'capitalize',
              }}
            >
              {mode}
            </button>
          ))}
        </div>
        {s.execution.mode === 'supervised' && (
          <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
            Every entry waits for your approval on the Dashboard. Exits, stops and the kill switch never wait.
          </div>
        )}
      </Card>

      <Card label="Universe" right={`${s.universe.symbols.length} symbols · long only`}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-2)' }}>
          {s.universe.symbols.map((symbol) => (
            <span
              key={symbol}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '8px',
                height: '32px',
                padding: '0 10px',
                borderRadius: 'var(--sa-radius-sm)',
                border: '1px solid var(--sa-border)',
                background: 'var(--sa-raised)',
                fontSize: 'var(--sa-text-md)',
              }}
            >
              <b style={{ fontWeight: 600 }}>{symbol}</b>
              <span style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-sm)' }}>
                {s.universe.sectors[symbol] ?? 'no sector'}
              </span>
              {!held.has(symbol) && (
                <button
                  type="button"
                  aria-label={`Remove ${symbol}`}
                  onClick={() => removeSymbol(symbol)}
                  style={{ background: 'none', border: 'none', color: 'var(--sa-text-muted)', cursor: 'pointer', padding: 0 }}
                >
                  ×
                </button>
              )}
            </span>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 'var(--sa-space-2)', flexWrap: 'wrap', alignItems: 'center' }}>
          <input
            aria-label="New symbol"
            placeholder="Ticker"
            value={newSymbol}
            onChange={(e) => setNewSymbol(e.target.value.toUpperCase())}
            style={{
              height: 'var(--sa-input-height)',
              width: '110px',
              padding: '0 12px',
              borderRadius: 'var(--sa-radius-md)',
              background: 'var(--sa-raised)',
              border: '1px solid var(--sa-border)',
              color: 'var(--sa-text)',
              fontFamily: 'var(--sa-font-mono)',
            }}
          />
          <select
            aria-label="Sector"
            value={newSector}
            onChange={(e) => setNewSector(e.target.value)}
            style={{
              height: 'var(--sa-input-height)',
              padding: '0 12px',
              borderRadius: 'var(--sa-radius-md)',
              background: 'var(--sa-raised)',
              border: '1px solid var(--sa-border)',
              color: newSector ? 'var(--sa-text)' : 'var(--sa-text-muted)',
            }}
          >
            <option value="">Sector…</option>
            {s.universe.allowed_sectors.map((sector) => (
              <option key={sector} value={sector}>{sector}</option>
            ))}
          </select>
          <Button onClick={addSymbol} disabled={!newSymbol.trim() || !newSector}>
            Add
          </Button>
        </div>
        <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          A new symbol is checked at Alpaca (active, tradable, US stock or ETF) when you save, then shows “Warming up” until its history loads.
        </div>
      </Card>

      <Card label="Risk limits" right="cash only · no margin">
        <Grid>
          <NumberField label="Risk per trade" unit="% of equity" help="Loss if the stop is hit." step="0.1" value={s.risk.risk_per_trade_pct} onChange={(e) => patch('risk', 'risk_per_trade_pct', Number(e.target.value))} />
          <NumberField label="Max position size" unit="% of equity" value={s.risk.max_position_pct} onChange={(e) => patch('risk', 'max_position_pct', Number(e.target.value))} />
          <NumberField label="Total open risk" unit="% of equity" help="Sum of entry-to-stop risk." step="0.5" value={s.risk.max_open_risk_pct} onChange={(e) => patch('risk', 'max_open_risk_pct', Number(e.target.value))} />
          <NumberField label="Max open positions" value={s.risk.max_open_positions} onChange={(e) => patch('risk', 'max_open_positions', Number(e.target.value))} />
          <NumberField label="Max positions per sector" help="QQQ counts as Technology, SPY as Index." value={s.risk.max_per_sector} onChange={(e) => patch('risk', 'max_per_sector', Number(e.target.value))} />
          <NumberField label="Daily loss limit" unit="%" help="No new entries for the rest of the session." step="0.5" value={s.risk.daily_loss_limit_pct} onChange={(e) => patch('risk', 'daily_loss_limit_pct', Number(e.target.value))} />
          <NumberField label="Losing streak" unit="trades" help="Then risk per trade is halved until a win." value={s.risk.losing_streak} onChange={(e) => patch('risk', 'losing_streak', Number(e.target.value))} />
          <NumberField label="Shock brake, last 1h" unit="% SPY drop" step="0.1" value={s.risk.shock_brake_1h_pct} onChange={(e) => patch('risk', 'shock_brake_1h_pct', Number(e.target.value))} />
          <NumberField label="Shock brake, since open" unit="% SPY drop" step="0.1" value={s.risk.shock_brake_session_pct} onChange={(e) => patch('risk', 'shock_brake_session_pct', Number(e.target.value))} />
          <NumberField label="Drawdown kill" unit="% from peak" help="Turns the kill switch on." value={s.risk.drawdown_kill_pct} onChange={(e) => patch('risk', 'drawdown_kill_pct', Number(e.target.value))} />
          <NumberField label="Max single order" unit="% of equity" value={s.risk.max_order_pct} onChange={(e) => patch('risk', 'max_order_pct', Number(e.target.value))} />
          <NumberField label="Max orders per cycle" value={s.risk.max_orders_per_cycle} onChange={(e) => patch('risk', 'max_orders_per_cycle', Number(e.target.value))} />
        </Grid>
      </Card>

      <Card label="Entry rules and checks">
        <Grid>
          <NumberField label="RSI from" value={s.rules.rsi_min} onChange={(e) => patch('rules', 'rsi_min', Number(e.target.value))} />
          <NumberField label="RSI to" value={s.rules.rsi_max} onChange={(e) => patch('rules', 'rsi_max', Number(e.target.value))} />
          <NumberField label="Min confidence to enter" step="0.05" value={s.decision.min_confidence_enter} onChange={(e) => patch('decision', 'min_confidence_enter', Number(e.target.value))} />
          <NumberField label="Stop distance from" unit="× ATR 1h" step="0.5" value={s.decision.stop_atr_min} onChange={(e) => patch('decision', 'stop_atr_min', Number(e.target.value))} />
          <NumberField label="Stop distance to" unit="× ATR 1h" step="0.5" value={s.decision.stop_atr_max} onChange={(e) => patch('decision', 'stop_atr_max', Number(e.target.value))} />
          <NumberField label="Min reward to risk" step="0.1" value={s.decision.min_reward_risk} onChange={(e) => patch('decision', 'min_reward_risk', Number(e.target.value))} />
          <NumberField label="Max spread" unit="% of price" step="0.05" value={s.execution.max_spread_pct} onChange={(e) => patch('execution', 'max_spread_pct', Number(e.target.value))} />
          <NumberField label="Entry limit above ask" unit="%" step="0.01" value={s.execution.entry_limit_offset_pct} onChange={(e) => patch('execution', 'entry_limit_offset_pct', Number(e.target.value))} />
          <NumberField label="Cancel unfilled entry after" unit="min" value={s.execution.entry_timeout_minutes} onChange={(e) => patch('execution', 'entry_timeout_minutes', Number(e.target.value))} />
        </Grid>
      </Card>

      <Card label="Earnings">
        <Grid>
          <NumberField label="Blackout before earnings" unit="trading days" help="No new entries inside it." value={s.risk.earnings_blackout_days} onChange={(e) => patch('risk', 'earnings_blackout_days', Number(e.target.value))} />
        </Grid>
        <Toggle
          label="Hold through earnings"
          checked={s.risk.hold_through_earnings}
          onChange={(v) => patch('risk', 'hold_through_earnings', v)}
        />
        <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          Off: a held symbol is sold in the last cycle before it reports.
        </div>
      </Card>

      <Card label="Schedule" right={`${data.schedule.cycles_per_session} cycles per session`}>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--sa-border)' }}>
                {['Cycle', 'New York', 'Amsterdam today', data.schedule.shifted_label ?? '', 'Bar used', 'New entries'].map((h, i) => (
                  <th key={i} style={{ padding: 'var(--sa-space-2) 10px', textAlign: 'left', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', fontWeight: 600 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {scheduleRows.map((row) => (
                <tr key={row.index} style={{ borderBottom: '1px solid var(--sa-border)' }}>
                  <td style={{ padding: 'var(--sa-space-2) 10px', color: 'var(--sa-text-muted)' }}>{row.index}</td>
                  <td style={{ padding: 'var(--sa-space-2) 10px', fontFamily: 'var(--sa-font-mono)' }}>{row.ny}</td>
                  <td style={{ padding: 'var(--sa-space-2) 10px', fontFamily: 'var(--sa-font-mono)' }}>{row.amsterdam}</td>
                  <td style={{ padding: 'var(--sa-space-2) 10px', fontFamily: 'var(--sa-font-mono)', color: 'var(--sa-text-muted)' }}>{row.amsterdam_shifted ?? '—'}</td>
                  <td style={{ padding: 'var(--sa-space-2) 10px', fontFamily: 'var(--sa-font-mono)', color: 'var(--sa-text-muted)' }}>{row.bar_used}</td>
                  <td style={{ padding: 'var(--sa-space-2) 10px', color: 'var(--sa-text-2)' }}>
                    {row.entries}
                    {row.entries === 'no' && <span style={{ color: 'var(--sa-text-muted)' }}> · exits and earnings exits only</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <label style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxWidth: '420px' }}>
          <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>Cycle times (New York)</span>
          <input
            aria-label="Cycle times"
            value={s.schedule.cycles.join(', ')}
            onChange={(e) =>
              patch('schedule', 'cycles', e.target.value.split(',').map((t) => t.trim()).filter(Boolean))
            }
            style={{
              height: 'var(--sa-input-height)',
              padding: '0 12px',
              borderRadius: 'var(--sa-radius-md)',
              background: 'var(--sa-raised)',
              border: '1px solid var(--sa-border)',
              color: 'var(--sa-text)',
              fontFamily: 'var(--sa-font-mono)',
            }}
          />
        </label>
      </Card>

      <Card label="Model">
        <Grid>
          <label style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>Model</span>
            <input
              aria-label="Model"
              value={s.decision.model}
              onChange={(e) => patch('decision', 'model', e.target.value)}
              style={{ height: 'var(--sa-input-height)', padding: '0 12px', borderRadius: 'var(--sa-radius-md)', background: 'var(--sa-raised)', border: '1px solid var(--sa-border)', color: 'var(--sa-text)', fontFamily: 'var(--sa-font-mono)' }}
            />
          </label>
          <NumberField label="Temperature" step="0.1" value={s.decision.temperature} onChange={(e) => patch('decision', 'temperature', Number(e.target.value))} />
          <label style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>Prompt version</span>
            <select
              aria-label="Prompt version"
              value={s.decision.prompt}
              onChange={(e) => patch('decision', 'prompt', e.target.value)}
              style={{ height: 'var(--sa-input-height)', padding: '0 12px', borderRadius: 'var(--sa-radius-md)', background: 'var(--sa-raised)', border: '1px solid var(--sa-border)', color: 'var(--sa-text)' }}
            >
              {[s.decision.prompt, ...data.prompt_options.map((p) => p.version)]
                .filter((v, i, a) => a.indexOf(v) === i)
                .map((v) => {
                  const opt = data.prompt_options.find((p) => p.version === v);
                  return <option key={v} value={v}>{opt ? `${opt.version} · ${opt.hash}` : v}</option>;
                })}
            </select>
          </label>
          <NumberField label="Max model calls per cycle" value={s.decision.max_calls_per_cycle} onChange={(e) => patch('decision', 'max_calls_per_cycle', Number(e.target.value))} />
        </Grid>
        <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          Prompts are never edited; a change is a new version.
        </div>
      </Card>

      <Card label="Paper realism">
        <Grid>
          <NumberField label="Assumed slippage" unit="% per side" help="Used for the adjusted P&L only. Orders are unaffected." step="0.01" value={s.performance.slippage_pct_per_side} onChange={(e) => patch('performance', 'slippage_pct_per_side', Number(e.target.value))} />
        </Grid>
      </Card>

      <Card label="Connections">
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--sa-border)' }}>
                {['Source', 'Status', 'Detail'].map((h) => (
                  <th key={h} style={{ padding: 'var(--sa-space-2) 10px', textAlign: 'left', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', fontWeight: 600 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.connections.map((c) => (
                <tr key={c.source} style={{ borderBottom: '1px solid var(--sa-border)' }}>
                  <td style={{ padding: 'var(--sa-space-2) 10px', fontWeight: 500 }}>{c.source}</td>
                  <td style={{ padding: 'var(--sa-space-2) 10px', fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-md)', color: c.status.includes('error') || c.status === 'down' ? 'var(--sa-negative-text)' : 'var(--sa-positive)' }}>{c.status}</td>
                  <td style={{ padding: 'var(--sa-space-2) 10px', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>{c.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card
        label="Settings history"
        right={data.active_version === null ? 'no saved versions' : `current: v${data.active_version}`}
      >
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--sa-border)' }}>
                {['Version', 'When', 'Change', 'Reason'].map((h) => (
                  <th key={h} style={{ padding: 'var(--sa-space-2) 10px', textAlign: 'left', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', fontWeight: 600 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.history.map((row) => (
                <tr key={row.version} style={{ borderBottom: '1px solid var(--sa-border)' }}>
                  <td style={{ padding: 'var(--sa-space-2) 10px', fontFamily: 'var(--sa-font-mono)' }}>v{row.version}</td>
                  <td style={{ padding: 'var(--sa-space-2) 10px', fontFamily: 'var(--sa-font-mono)', color: 'var(--sa-text-2)' }}>{displayDateTime(row.changed_at)}</td>
                  <td style={{ padding: 'var(--sa-space-2) 10px', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>{row.change}</td>
                  <td style={{ padding: 'var(--sa-space-2) 10px', fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>{row.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card label="Paper account">
        {resetStep2 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-3)' }}>
            <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>{resetStep2}</div>
            <div>
              <Button variant="primary" onClick={startRun} disabled={resetBusy}>
                Start new run
              </Button>
            </div>
          </div>
        ) : (
          <div>
            <Button variant="danger" onClick={() => setResetOpen(true)}>
              Reset paper account
            </Button>
          </div>
        )}
      </Card>

      <div
        style={{
          position: 'sticky',
          bottom: 0,
          display: 'flex',
          justifyContent: 'flex-end',
          gap: 'var(--sa-space-3)',
          padding: 'var(--sa-space-3) 0',
          background: 'var(--sa-bg)',
          borderTop: '1px solid var(--sa-border)',
        }}
      >
        <Button onClick={() => setForm(structuredClone(data.settings))} disabled={!dirty}>
          Discard
        </Button>
        <Button variant="primary" onClick={() => setSaveOpen(true)} disabled={!dirty}>
          Save as v{data.next_version}
        </Button>
      </div>

      <SaveSettingsDialog
        open={saveOpen}
        version={data.next_version}
        diffs={diffs}
        busy={saving}
        onCancel={() => setSaveOpen(false)}
        onConfirm={save}
      />

      <ResetAccountDialog
        open={resetOpen}
        busy={resetBusy}
        onCancel={() => setResetOpen(false)}
        onConfirm={doReset}
      />
    </div>
  );
}
