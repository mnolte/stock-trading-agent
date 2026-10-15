import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { getDecision, getDecisions } from '../api/state';
import { Card } from '../components/Card';
import type {
  DecisionDetail,
  DecisionListItem,
  DecisionsResponse,
} from '../types/api';
import {
  actionColor,
  contextLabel,
  formatAmsTime,
  formatCost,
  formatLatency,
  formatNyTitle,
  noCallReasonLabel,
  splitNote,
} from '../components/decisionFormat';

function SummaryStrip({ summary }: { summary: DecisionsResponse['summary'] }) {
  return (
    <div
      role="status"
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: '10px var(--sa-space-5)',
        padding: 'var(--sa-space-3) var(--sa-space-4)',
        background: 'var(--sa-panel)',
        border: '1px solid var(--sa-border)',
        borderRadius: 'var(--sa-radius-lg)',
        fontSize: 'var(--sa-text-md)',
        color: 'var(--sa-text-2)',
      }}
    >
      <span>
        Today:{' '}
        <b style={{ fontFamily: 'var(--sa-font-mono)', fontWeight: 500 }}>
          {summary.calls}
        </b>{' '}
        calls
      </span>
      <span>{summary.no_calls} no calls</span>
      <span>
        cost{' '}
        <b style={{ fontFamily: 'var(--sa-font-mono)', fontWeight: 500 }}>
          {formatCost(summary.cost)}
        </b>
      </span>
      <span>
        {summary.changed_by_code} changed by code
      </span>
      <span>
        prompt{' '}
        <span style={{ fontFamily: 'var(--sa-font-mono)' }}>
          {summary.prompt_version ?? '—'}
        </span>{' '}
        ·{' '}
        <span style={{ fontFamily: 'var(--sa-font-mono)' }}>
          {summary.prompt_hash ?? '—'}
        </span>
      </span>
      <span>
        model{' '}
        <span style={{ fontFamily: 'var(--sa-font-mono)' }}>
          {summary.model ?? '—'}
        </span>{' '}
        · temperature {summary.temperature ?? '—'}
      </span>
    </div>
  );
}

function CallRow({
  item,
  selected,
  onSelect,
}: {
  item: DecisionListItem;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      title={formatNyTitle(item.cycle_time)}
      style={{
        display: 'grid',
        gridTemplateColumns: '44px 56px 1fr auto',
        gap: 'var(--sa-space-2)',
        alignItems: 'center',
        minHeight: 'var(--sa-control-height)',
        padding: '0 10px',
        border: 0,
        borderRadius: 'var(--sa-radius-sm)',
        textAlign: 'left',
        cursor: 'pointer',
        fontSize: 'var(--sa-text-base)',
        color: 'var(--sa-text)',
        background: selected ? 'var(--sa-accent-bg)' : 'transparent',
        boxShadow: selected ? 'inset 3px 0 0 var(--sa-accent)' : 'none',
        fontFamily: 'inherit',
      }}
    >
      <span
        style={{
          fontFamily: 'var(--sa-font-mono)',
          fontSize: 'var(--sa-text-sm)',
          color: 'var(--sa-text-muted)',
        }}
      >
        {formatAmsTime(item.cycle_time)}
      </span>
      <span style={{ fontWeight: 600 }}>{item.symbol}</span>
      <span
        style={{
          fontFamily: 'var(--sa-font-mono)',
          fontSize: 'var(--sa-text-sm)',
          color: actionColor(item.final_action),
        }}
      >
        {item.final_action.replace('_', ' ')}
      </span>
      <span
        style={{
          fontSize: 'var(--sa-text-xs)',
          color:
            item.final_action === 'no_call'
              ? 'var(--sa-text-muted)'
              : 'var(--sa-warning-text)',
          textAlign: 'right',
        }}
      >
        {item.final_action === 'no_call' && item.no_call_reason
          ? noCallReasonLabel(item.no_call_reason)
          : item.changed_by_code
            ? 'changed by code'
            : ''}
      </span>
    </button>
  );
}

function StatBox({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        padding: '10px',
        border: '1px solid var(--sa-border)',
        borderRadius: 'var(--sa-radius-md)',
      }}
    >
      <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
        {label}
      </div>
      <div style={{ fontFamily: 'var(--sa-font-mono)', marginTop: '4px' }}>
        {value}
      </div>
    </div>
  );
}

function MonoPre({ children }: { children: string }) {
  return (
    <pre
      style={{
        margin: 0,
        padding: 'var(--sa-space-3)',
        background: 'var(--sa-bg)',
        border: '1px solid var(--sa-border)',
        borderRadius: 'var(--sa-radius-md)',
        fontFamily: 'var(--sa-font-mono)',
        fontSize: 'var(--sa-text-sm)',
        lineHeight: 1.55,
        color: 'var(--sa-text-2)',
        whiteSpace: 'pre-wrap',
        overflowX: 'auto',
      }}
    >
      {children}
    </pre>
  );
}

function CallDetail({ decision }: { decision: DecisionDetail }) {
  const validation = decision.validation_result;
  const ok =
    validation !== null &&
    validation.valid &&
    !validation.changed_by_code &&
    validation.error === null;
  const note = validation?.note ?? '';
  const [headline, rest] = splitNote(note);
  const price =
    typeof decision.input_snapshot?.price === 'number'
      ? decision.input_snapshot.price.toFixed(2)
      : '—';
  const tokens =
    decision.tokens_in !== null && decision.tokens_out !== null
      ? `${decision.tokens_in.toLocaleString('en-US')} / ${decision.tokens_out}`
      : '—';

  return (
    <>
      <section
        aria-label="Call details"
        style={{
          background: 'var(--sa-panel)',
          border: '1px solid var(--sa-border)',
          borderRadius: 'var(--sa-radius-lg)',
          padding: 'var(--sa-space-4) var(--sa-space-5)',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--sa-space-4)',
        }}
      >
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'baseline',
            gap: 'var(--sa-space-2) var(--sa-space-4)',
          }}
        >
          <h2 style={{ margin: 0, fontSize: 'var(--sa-text-xl)', fontWeight: 600 }}>
            {decision.symbol} · {formatAmsTime(decision.cycle_time)} cycle
          </h2>
          <span style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
            {contextLabel(decision.context)} · price {price}
          </span>
          <span style={{ flex: 1 }} />
          <Link
            to={`/decisions/${decision.id}`}
            style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-accent)' }}
          >
            Open decision
          </Link>
        </div>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
            gap: '10px',
          }}
        >
          <StatBox label="Tokens in / out" value={tokens} />
          <StatBox label="Cost" value={formatCost(decision.cost)} />
          <StatBox label="Latency" value={formatLatency(decision.latency_ms)} />
          <StatBox label="Prompt" value={decision.prompt_version ?? '—'} />
        </div>
        {validation && (
          <div
            style={{
              padding: '10px 12px',
              borderRadius: 'var(--sa-radius-md)',
              fontSize: 'var(--sa-text-md)',
              border: `1px solid ${ok ? 'var(--sa-positive-border)' : 'var(--sa-warning-border)'}`,
              background: ok ? 'var(--sa-positive-bg)' : 'var(--sa-warning-bg)',
              color: ok ? 'var(--sa-positive)' : 'var(--sa-warning-text)',
            }}
          >
            <b style={{ fontWeight: 600 }}>{headline}</b>
            {rest && <span style={{ color: 'var(--sa-text-2)' }}> {rest}</span>}
          </div>
        )}
      </section>
      <Card label="Prompt">
        <div>
          <div
            style={{
              fontSize: 'var(--sa-text-sm)',
              color: 'var(--sa-text-muted)',
              marginBottom: 'var(--sa-space-2)',
            }}
          >
            System
          </div>
          <MonoPre>{decision.prompt_text ?? '—'}</MonoPre>
        </div>
        <div>
          <div
            style={{
              fontSize: 'var(--sa-text-sm)',
              color: 'var(--sa-text-muted)',
              marginBottom: 'var(--sa-space-2)',
            }}
          >
            User · input snapshot
          </div>
          <MonoPre>
            {decision.input_snapshot
              ? JSON.stringify(decision.input_snapshot, null, 2)
              : '—'}
          </MonoPre>
        </div>
      </Card>
      <Card label="Raw response">
        <MonoPre>
          {decision.raw_response ?? 'No response — the call did not complete.'}
        </MonoPre>
      </Card>
    </>
  );
}

export default function Inspector() {
  const [data, setData] = useState<DecisionsResponse | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<DecisionDetail | null>(null);

  useEffect(() => {
    getDecisions()
      .then((result) => {
        setData(result);
        setSelectedId((current) => current ?? result.items[0]?.id ?? null);
      })
      .catch(console.error);
  }, []);

  useEffect(() => {
    if (selectedId === null) {
      setDetail(null);
      return;
    }
    getDecision(selectedId).then(setDetail).catch(console.error);
  }, [selectedId]);

  return (
    <div
      style={{
        maxWidth: 'var(--sa-page-max)',
        margin: '0 auto',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sa-space-5)',
      }}
    >
      {data && <SummaryStrip summary={data.summary} />}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 'var(--sa-space-5)',
          alignItems: 'flex-start',
        }}
      >
        <section
          aria-label="Model calls"
          style={{
            flex: '1 1 320px',
            maxWidth: '400px',
            minWidth: 0,
            background: 'var(--sa-panel)',
            border: '1px solid var(--sa-border)',
            borderRadius: 'var(--sa-radius-lg)',
            padding: 'var(--sa-space-3)',
            display: 'flex',
            flexDirection: 'column',
            gap: '2px',
            boxSizing: 'border-box',
          }}
        >
          <h2
            style={{
              margin: '4px 8px 10px',
              fontSize: 'var(--sa-text-md)',
              fontWeight: 600,
              color: 'var(--sa-text-2)',
              textTransform: 'uppercase',
              letterSpacing: 'var(--sa-label-tracking)',
            }}
          >
            Calls today
          </h2>
          {data?.items.length ? (
            data.items.map((item) => (
              <CallRow
                key={item.id}
                item={item}
                selected={item.id === selectedId}
                onSelect={() => setSelectedId(item.id)}
              />
            ))
          ) : (
            <div
              style={{
                padding: 'var(--sa-space-4)',
                color: 'var(--sa-text-muted)',
                fontSize: 'var(--sa-text-md)',
              }}
            >
              No model calls today.
            </div>
          )}
        </section>
        <div
          style={{
            flex: '999 1 560px',
            minWidth: 0,
            display: 'flex',
            flexDirection: 'column',
            gap: 'var(--sa-space-4)',
          }}
        >
          {detail ? (
            <CallDetail decision={detail} />
          ) : (
            <Card label="Call details">
              <div style={{ color: 'var(--sa-text-muted)', fontSize: 'var(--sa-text-md)' }}>
                Select a call to see the prompt, the input snapshot and the raw
                response.
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
