import { Link } from 'react-router-dom';

import type { Resource } from '../api/useResource';
import type { BriefingChip as ChipData, DashboardResponse } from '../types/api';
import { CardBody } from './cardState';
import { Chip } from './Chip';
import { formatCost } from './decisionFormat';

const DISPLAY_TZ = 'Europe/Amsterdam';

/** "Fri 16 Oct 15:00" — next write instant, Amsterdam time. */
function nextLabel(iso: string): string {
  const d = new Date(iso);
  const weekday = d.toLocaleDateString('en-GB', {
    weekday: 'short',
    timeZone: DISPLAY_TZ,
  });
  const day = d.toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    timeZone: DISPLAY_TZ,
  });
  const time = d.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: DISPLAY_TZ,
  });
  return `${weekday} ${day} ${time}`;
}

function chipVariant(tone: ChipData['tone']) {
  return tone;
}

/**
 * The dashboard's daily briefing card, next to the risk budget card
 * (docs/SPEC.md, GUI — Daily briefing card; design screens/Main).
 * Today's briefing once its slot has passed, the previous one with its
 * date before that; "off" with a Settings link when briefings are off.
 */
export function BriefingCard({ res }: { res: Resource<DashboardResponse> }) {
  const briefing = res.data?.briefing ?? null;
  const card = briefing?.card ?? null;
  const enabled = briefing?.enabled ?? true;
  const next = briefing?.next_at ? nextLabel(briefing.next_at) : null;
  const title = card ? `Daily briefing · ${card.today ? card.time : card.label}` : 'Daily briefing';
  return (
    <section
      aria-label="Daily briefing"
      aria-busy={res.loading || undefined}
      style={{
        flex: '999 1 460px',
        minWidth: 0,
        background: 'var(--sa-panel)',
        border: '1px solid var(--sa-border)',
        borderRadius: 'var(--sa-radius-lg)',
        padding: 'var(--sa-space-4) var(--sa-space-5)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sa-space-3)',
        boxSizing: 'border-box',
      }}
    >
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          gap: 'var(--sa-space-2)',
        }}
      >
        <h2
          style={{
            margin: 0,
            fontSize: 'var(--sa-text-sm)',
            fontWeight: 600,
            color: 'var(--sa-text-2)',
            textTransform: 'uppercase',
            letterSpacing: 'var(--sa-label-tracking)',
          }}
        >
          {title}
        </h2>
        <Link to="/reviews" style={{ fontSize: 'var(--sa-text-md)' }}>
          All briefings and reviews
        </Link>
      </div>
      <CardBody res={res} what="the briefing">
        {() => (
          <>
            {!enabled && (
              <p style={{ margin: 0, lineHeight: 1.65, color: 'var(--sa-text-2)' }}>
                Daily briefings are turned off — turn them back on in{' '}
                <Link to="/settings">Settings</Link>.
              </p>
            )}
            {enabled && card === null && (
              <p style={{ margin: 0, lineHeight: 1.65, color: 'var(--sa-text-2)' }}>
                No briefing written yet
                {next ? ` — the next is written ${next}` : ''}.
              </p>
            )}
            {enabled && card !== null && (
              <>
                <p style={{ margin: 0, lineHeight: 1.65, color: 'var(--sa-text-2)' }}>
                  {card.text}
                </p>
                {card.chips.length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sa-space-2)' }}>
                    {card.chips.map((chip, i) => (
                      <Chip key={i} variant={chipVariant(chip.tone)}>
                        {chip.text}
                      </Chip>
                    ))}
                  </div>
                )}
                <div style={{ flex: 1 }} />
                <div
                  style={{
                    display: 'flex',
                    flexWrap: 'wrap',
                    gap: 'var(--sa-space-2) var(--sa-space-4)',
                    paddingTop: 'var(--sa-space-3)',
                    borderTop: '1px solid var(--sa-border)',
                    fontSize: 'var(--sa-text-sm)',
                    color: 'var(--sa-text-muted)',
                  }}
                >
                  <span>
                    {card.source === 'model'
                      ? 'Numbers checked ✓'
                      : `Plain briefing: ${card.reason ?? 'template used'}`}
                  </span>
                  <span>
                    {card.source === 'model' ? card.model ?? 'model' : 'template'}
                    {card.prompt_version ? ` · ${card.prompt_version}` : ''}
                  </span>
                  {card.cost !== null && <span>{formatCost(card.cost)}</span>}
                  {next && <span>Next briefing {next}</span>}
                </div>
                <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
                  {card.source === 'model'
                    ? 'Written by the model from figures computed by the agent before the open; every number was checked against those figures. Things that changed since are in the cards around it.'
                    : 'Built from the same figures with the plain template. Things that changed since are in the cards around it.'}
                </div>
              </>
            )}
          </>
        )}
      </CardBody>
    </section>
  );
}
