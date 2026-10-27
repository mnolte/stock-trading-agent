import { useCallback, useEffect, useMemo, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { errorMessages } from '../api/client';
import { useLiveUpdates } from '../api/liveContext';
import { getStrategies, postStrategy, postStrategyAction } from '../api/state';
import { Banner } from '../components/Banner';
import { Card } from '../components/Card';
import { Chip } from '../components/Chip';
import type { ChipVariant } from '../components/Chip';
import { DialogButton, Modal } from '../components/Modal';
import { SegmentedControl } from '../components/SegmentedControl';
import { StrategyEquityChart } from '../components/StrategyEquityChart';
import type { StrategySeries } from '../components/StrategyEquityChart';
import type { NewStrategyBody, StrategiesResponse, StrategyRow } from '../types/api';
import { numVariant, signedPct, signedPts, signedR } from './reviewsFormat';
import {
  cost,
  differsLine,
  disagreementFooter,
  drawdown,
  num,
  openPositionsLine,
  rowColours,
  sameAsLive,
  sameLine,
  sampleWarning,
  sessionDay,
  shortDate,
  startedLine,
} from './strategiesFormat';

const TONE: Record<'positive' | 'negative' | 'neutral', string> = {
  positive: 'var(--sa-positive)',
  negative: 'var(--sa-negative-text)',
  neutral: 'var(--sa-text-2)',
};

const ROLE_CHIP: Record<StrategyRow['role'], ChipVariant> = {
  live: 'warning',
  shadow: 'info',
  baseline: 'neutral',
};

const SAME_AS_LIVE_RULE =
  'Share of symbol-cycles where this strategy chose the same final action as live. ' +
  'Counted only when at least one side did more than a rules no_call; every no_call reason is one action; missed shadow work is left out.';

const th: CSSProperties = {
  textAlign: 'right',
  padding: '8px 10px',
  fontSize: 'var(--sa-text-sm)',
  fontWeight: 500,
  color: 'var(--sa-text-muted)',
  borderBottom: '1px solid var(--sa-border)',
  whiteSpace: 'nowrap',
};
const td: CSSProperties = {
  padding: '11px 10px',
  borderBottom: '1px solid var(--sa-border)',
  fontSize: 'var(--sa-text-md)',
  verticalAlign: 'middle',
  textAlign: 'right',
  fontFamily: 'var(--sa-font-mono)',
};

function KeyRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        gap: 'var(--sa-space-3)',
        padding: '8px 0',
        borderBottom: '1px solid var(--sa-subtle)',
        fontSize: 'var(--sa-text-md)',
      }}
    >
      <span style={{ color: 'var(--sa-text-muted)' }}>{label}</span>
      <span style={{ color: 'var(--sa-text-2)', textAlign: 'right' }}>{children}</span>
    </div>
  );
}

function OutlineButton(props: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      {...props}
      style={{
        height: 'var(--sa-input-height)',
        padding: '0 var(--sa-space-4)',
        borderRadius: 'var(--sa-radius-md)',
        border: '1px solid var(--sa-border)',
        background: 'transparent',
        color: props.disabled ? 'var(--sa-text-disabled)' : 'var(--sa-text-2)',
        fontSize: 'var(--sa-text-base)',
        fontWeight: 600,
        cursor: props.disabled ? 'not-allowed' : 'pointer',
        whiteSpace: 'nowrap',
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// New shadow dialog

function NewShadowDialog({
  data,
  onClose,
  onCreated,
}: {
  data: StrategiesResponse;
  onClose: () => void;
  onCreated: (id: number) => void;
}) {
  const live = data.options.live;
  const [name, setName] = useState('');
  const [kind, setKind] = useState<'llm' | 'rules_only'>('llm');
  const [prompt, setPrompt] = useState(live.prompt_version);
  const [model, setModel] = useState(
    data.options.models.includes(live.model) ? live.model : (data.options.models[0] ?? ''),
  );
  const [temperature, setTemperature] = useState(String(live.temperature));
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const submit = () => {
    const body: NewStrategyBody = { name: name.trim(), kind };
    if (kind === 'llm') {
      body.prompt_version = prompt;
      body.model = model;
      body.temperature = Number(temperature);
    }
    const chosen: Record<string, number> = {};
    for (const option of data.options.overrides) {
      const raw = overrides[option.key];
      if (raw === undefined || raw.trim() === '') continue;
      if (kind === 'rules_only' && option.check) continue;
      chosen[option.key] = Number(raw);
    }
    if (Object.keys(chosen).length > 0) body.overrides = chosen;
    setBusy(true);
    setErrors([]);
    postStrategy(body)
      .then((res) => onCreated(res.id))
      .catch((e) => setErrors(errorMessages(e)))
      .finally(() => setBusy(false));
  };

  const input: CSSProperties = {
    height: 'var(--sa-input-height)',
    padding: '0 12px',
    borderRadius: 'var(--sa-radius-md)',
    background: 'var(--sa-raised)',
    border: '1px solid var(--sa-border)',
    color: 'var(--sa-text)',
    fontSize: 'var(--sa-text-base)',
  };
  const label: CSSProperties = { display: 'flex', flexDirection: 'column', gap: '6px', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' };

  return (
    <Modal
      open
      title="New shadow"
      onClose={onClose}
      footer={
        <>
          <DialogButton variant="cancel" onClick={onClose}>
            Cancel
          </DialogButton>
          <DialogButton variant="accent" onClick={submit} disabled={busy || !name.trim()}>
            Create shadow
          </DialogButton>
        </>
      }
    >
      <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.5 }}>
        Runs after each live cycle on the same inputs and never trades. Risk gates, sizing, the re-entry
        rule and earnings handling are always live's.
      </div>
      {errors.length > 0 && <Banner variant="negative">{errors.join(' ')}</Banner>}
      <label style={label}>
        Name
        <input aria-label="Name" style={input} value={name} maxLength={64} placeholder="C · Lower confidence" onChange={(e) => setName(e.target.value)} />
      </label>
      <div style={label}>
        Kind
        <SegmentedControl
          label="Kind"
          options={['LLM', 'Rules only']}
          value={kind === 'llm' ? 'LLM' : 'Rules only'}
          onChange={(o) => setKind(o === 'LLM' ? 'llm' : 'rules_only')}
        />
      </div>
      {kind === 'llm' ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 'var(--sa-space-3)' }}>
          <label style={label}>
            Prompt version
            <select aria-label="Prompt version" style={input} value={prompt} onChange={(e) => setPrompt(e.target.value)}>
              {data.options.prompts.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>
          <label style={label}>
            Model
            <select aria-label="Model" style={input} value={model} onChange={(e) => setModel(e.target.value)}>
              {data.options.models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
          <label style={label}>
            Temperature
            <input aria-label="Temperature" type="number" min={0} max={2} step={0.1} style={{ ...input, fontFamily: 'var(--sa-font-mono)' }} value={temperature} onChange={(e) => setTemperature(e.target.value)} />
          </label>
        </div>
      ) : (
        <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
          No model: enters whenever the rules pass, stop {data.rules_only.stop_atr} × ATR(1h) below entry, target{' '}
          {data.rules_only.target_r}R; exits only through stop, target or the earnings exit.
        </div>
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          Rule overrides · empty = same as live
        </span>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 'var(--sa-space-3)' }}>
          {data.options.overrides
            .filter((o) => kind === 'llm' || !o.check)
            .map((o) => (
              <label key={o.key} style={label}>
                {o.label}
                <input
                  aria-label={o.label}
                  type="number"
                  min={o.min ?? undefined}
                  max={o.max ?? undefined}
                  step="any"
                  placeholder={String(o.live)}
                  style={{ ...input, fontFamily: 'var(--sa-font-mono)' }}
                  value={overrides[o.key] ?? ''}
                  onChange={(e) => setOverrides((cur) => ({ ...cur, [o.key]: e.target.value }))}
                />
              </label>
            ))}
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------

export default function Strategies() {
  const [data, setData] = useState<StrategiesResponse | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [dialog, setDialog] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const navigate = useNavigate();

  const refresh = useCallback(() => {
    getStrategies()
      .then(setData)
      .catch((e) => setErrors(errorMessages(e)));
  }, []);
  useEffect(refresh, [refresh]);
  useLiveUpdates(['strategies', 'performance', 'decisions'], refresh);

  const rows = useMemo(() => data?.rows ?? [], [data]);
  const colours = useMemo(() => rowColours(rows), [rows]);
  const shadows = rows.filter((r) => r.id !== null);
  const selected =
    shadows.find((r) => r.id === selectedId) ?? shadows[0] ?? null;

  const series: StrategySeries[] = useMemo(() => {
    const out: StrategySeries[] = rows.map((r, i) => ({
      name: r.name,
      colour: colours[i].hex,
      points: r.curve.map((p) => ({ date: p.date, pct: Number(p.pct) })),
    }));
    out.push({
      name: 'SPY',
      colour: '#C9CFD6',
      dashed: true,
      points: (data?.spy_curve ?? []).map((p) => ({ date: p.date, pct: Number(p.pct) })),
    });
    return out;
  }, [rows, colours, data]);

  if (!data) {
    return errors.length > 0 ? <Banner variant="negative">{errors.join(' ')}</Banner> : null;
  }

  const since = data.run ? shortDate(data.run.started_at) : '—';
  const warning = sampleWarning(data.run?.sessions ?? null, rows, data.promote_min_closed_trades);
  const slotsFull = data.slots.used >= data.slots.max;

  const act = (id: number, action: 'pause' | 'resume' | 'archive') => {
    setErrors([]);
    postStrategyAction(id, action)
      .then(() => {
        setArchiveOpen(false);
        refresh();
      })
      .catch((e) => setErrors(errorMessages(e)));
  };

  const short = selected?.short ?? selected?.name ?? '';
  const onlyModel =
    selected?.differences?.differs.length === 1 && selected.differences.differs[0].key === 'decision.model';

  return (
    <div style={{ maxWidth: 'var(--sa-page-max)', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 'var(--sa-space-5)' }}>
      {errors.length > 0 && <Banner variant="negative">{errors.join(' ')}</Banner>}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: '10px 20px',
          padding: '14px 18px',
          background: 'var(--sa-panel)',
          border: '1px solid var(--sa-border)',
          borderRadius: 'var(--sa-radius-lg)',
        }}
      >
        <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)' }}>
          Shadows never trade. They see exactly what live saw each cycle, decide after it, and fill on the same
          5-min bars with the same slippage.
        </span>
        <span style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }} data-testid="synthetic-quote-count">
          Live quotes use live entry checks; synthetic quotes assume no spread.{' '}
          {data.synthetic_quote_entries} synthetic-quote entries this run.
        </span>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }} data-testid="shadow-cost">
          Shadow model cost{' '}
          <span style={{ fontFamily: 'var(--sa-font-mono)', color: 'var(--sa-text-2)' }}>
            ${Number(data.cost.month_usd).toFixed(2)} of ${Number(data.cost.cap_usd)}
          </span>{' '}
          this month · {data.slots.used} of {data.slots.max} slots
        </span>
        {data.cost.cap_reached && <Chip variant="warning">LLM shadows paused until next month</Chip>}
        <OutlineButton
          onClick={() => setDialog(true)}
          disabled={slotsFull}
          title={slotsFull ? `All ${data.slots.max} slots are in use; archive a shadow first` : undefined}
        >
          New shadow…
        </OutlineButton>
      </div>

      <Card label="Compare" right={<div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>this run · since {since}</div>}>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', minWidth: '900px', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th scope="col" style={{ ...th, textAlign: 'left' }}>Strategy</th>
                <th scope="col" style={{ ...th, textAlign: 'left' }} />
                <th scope="col" style={th} title="Adjusted: slippage per side and dividends, for live and shadows alike">
                  Since {since} · adjusted
                </th>
                <th scope="col" style={th}>vs SPY</th>
                <th scope="col" style={th}>Closed trades</th>
                <th scope="col" style={th}>Average R</th>
                <th scope="col" style={th}>Max drawdown</th>
                <th scope="col" style={th} title={SAME_AS_LIVE_RULE}>Same as live</th>
                <th scope="col" style={th}>Model cost</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const isSelected = selected !== null && r.id === selected.id && r.id !== null;
                const ret = num(r.return_pct);
                const vs = num(r.vs_spy_pts);
                return (
                  <tr
                    key={r.id ?? 'live'}
                    data-testid={`strategy-row-${r.id ?? 'live'}`}
                    onClick={() => r.id !== null && setSelectedId(r.id)}
                    style={{
                      cursor: r.id !== null ? 'pointer' : 'default',
                      background: isSelected ? 'var(--sa-accent-bg)' : undefined,
                      boxShadow: isSelected ? `inset 3px 0 0 ${colours[i].css}` : undefined,
                    }}
                  >
                    <td style={{ ...td, textAlign: 'left', fontFamily: 'var(--sa-font-sans)' }}>
                      <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                        <span style={{ width: 10, height: 10, borderRadius: 3, background: colours[i].css, flex: '0 0 10px' }} />
                        <div>
                          <div style={{ fontWeight: 600 }}>{r.name}</div>
                          <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>{r.description}</div>
                        </div>
                      </div>
                    </td>
                    <td style={{ ...td, textAlign: 'left' }}>
                      <span style={{ display: 'inline-flex', gap: 6 }}>
                        <Chip variant={ROLE_CHIP[r.role]}>{r.role}</Chip>
                        {r.status === 'paused' && <Chip variant="neutral">paused</Chip>}
                      </span>
                    </td>
                    <td
                      style={{ ...td, color: TONE[numVariant(ret)] }}
                      title={r.role === 'live' && r.raw_return_pct !== null ? `Raw paper return ${signedPct(num(r.raw_return_pct))}` : undefined}
                    >
                      {signedPct(ret)}
                    </td>
                    <td style={{ ...td, color: TONE[numVariant(vs)] }}>{signedPts(vs)}</td>
                    <td style={{ ...td, fontFamily: 'var(--sa-font-sans)' }}>
                      <span style={{ fontFamily: 'var(--sa-font-mono)' }}>{r.closed}</span>
                      <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                        {r.won} won · {r.lost} lost
                      </div>
                    </td>
                    <td style={td}>{signedR(num(r.avg_r))}</td>
                    <td style={td}>{drawdown(r.max_drawdown_pct)}</td>
                    <td style={td} title={SAME_AS_LIVE_RULE}>{sameAsLive(r.same_as_live_pct, r.same_as_live_count)}</td>
                    <td style={td}>{cost(r.model_cost)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {warning && <Banner variant="warning">{warning}</Banner>}
      </Card>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-5)', alignItems: 'stretch' }}>
        <div style={{ flex: '3 1 520px', minWidth: 0, display: 'flex' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <Card label={`Equity since ${since}`} right={<div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>% of starting equity</div>}>
              <StrategyEquityChart series={series} />
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 18px', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-2)' }}>
                {series.map((s) => (
                  <span key={s.name} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <span
                      style={
                        s.dashed
                          ? { width: 14, height: 0, borderTop: `2px dashed ${s.colour}` }
                          : { width: 14, height: 3, borderRadius: 2, background: s.colour }
                      }
                    />
                    {s.name}
                  </span>
                ))}
              </div>
            </Card>
          </div>
        </div>
        <div style={{ flex: '2 1 360px', minWidth: 0, display: 'flex' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            {selected ? (
              <Card label={selected.name} right={<Chip variant={ROLE_CHIP[selected.role]}>{selected.role}</Chip>}>
                <div>
                  <KeyRow label="Differs from live">
                    <span style={{ fontFamily: 'var(--sa-font-mono)' }}>{differsLine(selected.differences?.differs ?? [])}</span>
                  </KeyRow>
                  <KeyRow label="Same as live">{sameLine(selected.differences?.same ?? [])}</KeyRow>
                  <KeyRow label="Started">{startedLine(selected.started_at, selected.starting_equity)}</KeyRow>
                  <KeyRow label="Calls">
                    {selected.kind === 'llm'
                      ? `${selected.calls ?? 0} · ${cost(selected.model_cost)} · runs after each live cycle`
                      : 'no model · runs after each live cycle'}
                    {selected.missed ? ` · ${selected.missed} missed` : ''}
                  </KeyRow>
                  <KeyRow label="Open virtual positions">{openPositionsLine(selected.open_positions)}</KeyRow>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, paddingTop: 12 }}>
                  <OutlineButton onClick={() => act(selected.id as number, selected.status === 'paused' ? 'resume' : 'pause')}>
                    {selected.status === 'paused' ? 'Resume' : 'Pause'}
                  </OutlineButton>
                  <OutlineButton onClick={() => setArchiveOpen(true)}>Archive</OutlineButton>
                  <OutlineButton
                    disabled={!selected.promote?.allowed}
                    title={selected.promote?.reason ?? undefined}
                    onClick={() => navigate(`/settings?promote=${selected.id}`)}
                  >
                    Promote to live…
                  </OutlineButton>
                </div>
                <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', paddingTop: 6 }}>
                  Promote opens Settings with {onlyModel ? 'this model' : "this shadow's differences"} filled in as a new
                  settings version; nothing changes until you save it with a reason.
                </div>
              </Card>
            ) : (
              <Card label="No shadows yet">
                <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.5 }}>
                  A shadow runs another prompt, model or rule set — or no model at all — on exactly the inputs live
                  saw, after every live cycle. Up to {data.slots.max} at a time.
                </div>
              </Card>
            )}
          </div>
        </div>
      </div>

      {selected && selected.disagreements && (
        <Card
          label={`Where ${short} and live disagreed`}
          right={
            selected.disagreements.items.length > 0 ? (
              <Link to={`/decisions/${selected.disagreements.items[0].live_decision_id}`} style={{ fontSize: 'var(--sa-text-md)' }}>
                Open a decision
              </Link>
            ) : undefined
          }
        >
          {selected.disagreements.items.length === 0 && (
            <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>No disagreements yet.</div>
          )}
          {selected.disagreements.items.map((d) => (
            <Link
              key={`${d.time}-${d.symbol}`}
              to={`/decisions/${d.live_decision_id}`}
              style={{
                display: 'grid',
                gridTemplateColumns: '96px 60px 1fr auto',
                gap: 10,
                alignItems: 'baseline',
                padding: '10px 0',
                borderBottom: '1px solid var(--sa-border)',
                fontSize: 'var(--sa-text-md)',
                color: 'var(--sa-text)',
                textDecoration: 'none',
              }}
            >
              <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                {sessionDay(d.date)}
              </span>
              <b style={{ fontWeight: 600 }}>{d.symbol}</b>
              <span style={{ color: 'var(--sa-text-2)' }}>
                live {d.live_label} · {short} {d.shadow_label}
              </span>
              <span style={{ fontFamily: 'var(--sa-font-mono)', fontSize: 'var(--sa-text-sm)', color: d.tone === 'neutral' ? 'var(--sa-text-muted)' : TONE[d.tone] }}>
                {d.result}
              </span>
            </Link>
          ))}
          <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', paddingTop: 8 }}>
            {disagreementFooter(selected.disagreements.total, selected.disagreements.sessions)}
          </div>
        </Card>
      )}

      {dialog && (
        <NewShadowDialog
          data={data}
          onClose={() => setDialog(false)}
          onCreated={(id) => {
            setDialog(false);
            setSelectedId(id);
            refresh();
          }}
        />
      )}
      <Modal
        open={archiveOpen && selected !== null}
        title={`Archive ${selected?.name ?? ''}?`}
        onClose={() => setArchiveOpen(false)}
        footer={
          <>
            <DialogButton variant="cancel" onClick={() => setArchiveOpen(false)}>
              Cancel
            </DialogButton>
            <DialogButton variant="accent" onClick={() => selected && act(selected.id as number, 'archive')}>
              Archive
            </DialogButton>
          </>
        }
      >
        <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.5 }}>
          It stops running and frees its slot. Its decisions stay stored; it can't be resumed.
        </div>
      </Modal>
    </div>
  );
}
