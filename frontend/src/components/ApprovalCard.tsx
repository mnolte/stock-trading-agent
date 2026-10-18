import { useState } from 'react';
import { Link } from 'react-router-dom';

import { postApprove, postRejectApproval } from '../api/state';
import { displayTime } from '../format';
import type { Approval } from '../types/api';
import { Banner } from './Banner';
import { Button } from './Button';

/** R/R and dollar risk, computed from the stored limit/stop/target. */
function RiskLine({ approval }: { approval: Approval }) {
  const limit = Number(approval.limit_price);
  const stop = Number(approval.stop_price);
  const target = Number(approval.target_price);
  const risk = approval.qty * (limit - stop);
  const rr = limit - stop > 0 ? (target - limit) / (limit - stop) : null;
  return (
    <>
      {rr !== null && ` · R/R ${rr.toFixed(1)}`}
      {risk > 0 && ` · risk $${risk.toFixed(2)}`}
    </>
  );
}

/** Pending supervised-mode approval card (design/screens/States.dc.html).
 * Approve re-checks size and gates server-side on a fresh quote; it is
 * disabled while the kill switch is on. */
export function ApprovalCard({
  approval,
  killSwitchOn,
  paused = false,
  onResolved,
}: {
  approval: Approval;
  killSwitchOn: boolean;
  paused?: boolean;
  onResolved: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const act = (call: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    call()
      .then(onResolved)
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <div
      data-testid={`approval-${approval.id}`}
      style={{
        padding: 'var(--sa-space-4) var(--sa-space-5)',
        background: 'var(--sa-accent-bg)',
        border: '1px solid var(--sa-accent-border)',
        borderRadius: 'var(--sa-radius-lg)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sa-space-3)',
      }}
    >
      <div
        style={{
          fontSize: 'var(--sa-text-sm)',
          color: 'var(--sa-accent)',
          textTransform: 'uppercase',
          letterSpacing: 'var(--sa-label-tracking)',
          fontWeight: 600,
        }}
      >
        Pending approval · supervised mode
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 'var(--sa-space-3)', flexWrap: 'wrap' }}>
        <span
          style={{
            fontFamily: 'var(--sa-font-mono)',
            fontWeight: 600,
            color: 'var(--sa-positive)',
          }}
        >
          ENTER
        </span>
        <span style={{ fontWeight: 600 }}>
          {approval.symbol} · {approval.qty} shares · limit {approval.limit_price}
        </span>
      </div>
      <div
        style={{
          fontFamily: 'var(--sa-font-mono)',
          fontSize: 'var(--sa-text-md)',
          color: 'var(--sa-text-2)',
        }}
      >
        stop {approval.stop_price} · target {approval.target_price}
        <RiskLine approval={approval} />
      </div>
      <div style={{ fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-muted)' }}>
        Waiting since {displayTime(approval.created_at)}. Size and gates are checked
        again when you approve.
      </div>
      {error && <Banner variant="negative">{error}</Banner>}
      <div style={{ display: 'flex', gap: 'var(--sa-space-2)', alignItems: 'center' }}>
        <Link
          to={`/decisions/${approval.decision_id}`}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            height: 'var(--sa-control-height)',
            padding: '0 var(--sa-space-4)',
            borderRadius: 'var(--sa-radius-md)',
            border: '1px solid var(--sa-border-strong)',
            color: 'var(--sa-text)',
            fontSize: 'var(--sa-text-base)',
            fontWeight: 500,
          }}
        >
          Details
        </Link>
        <Button
          onClick={() => act(() => postRejectApproval(approval.id))}
          disabled={busy}
        >
          Reject
        </Button>
        <Button
          variant="primary"
          onClick={() => act(() => postApprove(approval.id))}
          disabled={busy || killSwitchOn || paused}
          title={
            killSwitchOn
              ? 'Kill switch is on'
              : paused
                ? 'New entries are paused'
                : undefined
          }
        >
          Approve
        </Button>
      </div>
    </div>
  );
}
