import { expect, type Page } from '@playwright/test';

import { test } from './shots';

// 046 scripted check: every banner kind from GET /api/banners
// (backend/app/api/routes.py banners()) and every Needs you kind from
// GET /api/attention (backend/app/attention.py) renders. Responses are
// mocked — this is a render matrix, not a seed exercise; E2E_DRAWER_ONLY=1
// runs it against Vite alone.

async function mockBackground(page: Page) {
  await page.routeWebSocket('**/ws', (socket) => socket.close());
  await page.route(/\/api\//, (route) => {
    if (!new URL(route.request().url()).pathname.startsWith('/api/')) return route.fallback();
    if (route.request().url().endsWith('/auth/status'))
      return route.fulfill({ json: { required: false, signed_in: true } });
    return route.fulfill({ status: 503, json: { detail: 'Isolated state test' } });
  });
}

async function mockBanners(page: Page, items: object[]) {
  await page.route('**/api/banners', (route) => route.fulfill({ json: { items } }));
}

// One representative item per banner kind, in routes.py order. `expect`
// is the text BannerStrip must show; `actions` are the buttons the kind's
// actions array produces (BannerStrip.tsx).
const BANNER_CASES: {
  kind: string;
  item: Record<string, unknown>;
  expect: string | RegExp;
  actions?: string[];
}[] = [
  {
    kind: 'shutting_down',
    item: { id: 'shutting-down', kind: 'shutting_down', level: 'warning', actions: [],
      message: 'The agent is shutting down — writes are refused; the last steps may still be finishing.' },
    expect: 'The agent is shutting down',
  },
  {
    kind: 'unclean_run',
    item: { id: null, kind: 'unclean_run', level: 'warning',
      message: 'The previous run did not shut down cleanly. Database integrity check ok · reconciliation with Alpaca: no differences · 1 cycle marked interrupted.',
      actions: ['open_logs', 'dismiss'] },
    expect: 'did not shut down cleanly',
    actions: ['Open logs', 'Dismiss'],
  },
  {
    kind: 'another_instance',
    item: { id: 11, kind: 'another_instance', level: 'critical',
      message: 'Another instance is trading this Alpaca account — the kill switch is on.',
      actions: ['acknowledge'] },
    expect: 'Another instance is trading',
    actions: ['Acknowledge'],
  },
  {
    kind: 'reconciliation',
    item: { id: 12, kind: 'reconciliation', level: 'critical',
      message: 'KO: Alpaca shows 7 shares, the database shows 5.',
      actions: ['review', 'acknowledge'] },
    // BannerStrip prefixes the stored message.
    expect: /Reconciliation mismatch — Alpaca shows something different\. KO: Alpaca shows/,
    actions: ['Review', 'Acknowledge'],
  },
  {
    kind: 'protective_stop',
    item: { id: 13, kind: 'protective_stop', level: 'critical',
      message: 'KO had no stop order at Alpaca — a protective stop at 88.50 was placed.',
      position_id: 3,
      actions: ['open_orders', 'acknowledge'] },
    expect: 'protective stop at 88.50',
    actions: ['Open orders', 'Acknowledge'],
  },
  {
    kind: 'pending_exit',
    item: { id: 14, kind: 'pending_exit', level: 'critical',
      message: 'KO exit is held back: legs still working.',
      actions: ['open_orders', 'acknowledge'] },
    expect: 'held back',
    actions: ['Open orders', 'Acknowledge'],
  },
  {
    kind: 'flatten_sell_rejected',
    item: { id: 15, kind: 'flatten_sell_rejected', level: 'critical',
      message: 'Stop & flatten: Alpaca rejected the KO sale.',
      actions: ['acknowledge'] },
    expect: 'flatten',
    actions: ['Acknowledge'],
  },
  {
    kind: 'flatten_sell_blocked',
    item: { id: 16, kind: 'flatten_sell_blocked', level: 'critical',
      message: 'Stop & flatten: the KO sale could not be sent.',
      actions: ['acknowledge'] },
    expect: 'flatten',
    actions: ['Acknowledge'],
  },
  {
    kind: 'restart_resumed',
    item: { id: 17, kind: 'restart_resumed', level: 'info',
      message: 'Restarted at 15:02 and resumed — trading continued.',
      actions: ['acknowledge'] },
    expect: 'Restarted at 15:02',
    actions: ['Acknowledge'],
  },
  {
    kind: 'restart_paused',
    item: { id: 18, kind: 'restart_paused', level: 'warning',
      message: 'Restarted and stayed paused: the previous stop was unclean.',
      resume_blocked: false,
      actions: ['open_logs', 'resume_pause', 'acknowledge'] },
    expect: 'Restarted and stayed paused',
    actions: ['Open logs', 'Resume', 'Acknowledge'],
  },
  {
    kind: 'cycle_slow',
    item: { id: 19, kind: 'cycle_slow', level: 'warning',
      message: 'The 14:46 cycle took 9 minutes.',
      actions: ['acknowledge'] },
    expect: 'cycle took 9 minutes',
    actions: ['Acknowledge'],
  },
  {
    kind: 'universe_screen_pending',
    item: { id: 20, kind: 'universe_screen_pending', level: 'warning',
      message: 'Universe screen did not run today; last list kept.',
      actions: ['acknowledge'] },
    expect: 'Universe screen did not run',
    actions: ['Acknowledge'],
  },
  {
    kind: 'broker_guard_unconfirmed',
    item: { id: 21, kind: 'broker_guard_unconfirmed', level: 'warning',
      message: 'Broker guard not confirmed: no_shorting/max_margin_multiplier could not be read back.',
      actions: ['acknowledge'] },
    expect: 'Broker guard not confirmed',
    actions: ['Acknowledge'],
  },
  {
    kind: 'universe_screen_failed',
    item: { id: 22, kind: 'universe_screen_failed', level: 'warning',
      message: 'Universe screen failed: Alpaca assets read failed.',
      actions: ['acknowledge'] },
    expect: 'Universe screen failed',
    actions: ['Acknowledge'],
  },
  {
    // The generic fallback: kind is the raw event_type (routes.py).
    kind: 'position_checks_failing',
    item: { id: 23, kind: 'position_checks_failing', level: 'warning',
      message: 'Position checks failing (3 in a row).',
      actions: ['acknowledge'] },
    expect: 'Position checks failing',
    actions: ['Acknowledge'],
  },
  {
    kind: 'unresolved_error (dismiss with note)',
    item: { id: 24, kind: 'unresolved_error', level: 'error',
      message: 'order_rejected. Dismiss with a note or it resolves itself after 30 minutes without recurring.',
      actions: ['dismiss'] },
    expect: 'Dismiss with a note',
    actions: ['Dismiss…'],
  },
  {
    kind: 'unresolved_error (state, no dismiss)',
    item: { id: 25, kind: 'unresolved_error', level: 'critical',
      message: 'stream_degraded. It resolves when the state clears; a dismiss while it lasts is refused.',
      actions: [] },
    expect: 'resolves when the state clears',
  },
  {
    kind: 'paused',
    item: { id: null, kind: 'paused', level: 'info',
      message: 'Started paused at 14:02. Reconciled with Alpaca: no open positions. Held positions are still reviewed each cycle; no new entries until you resume.',
      since: '2026-10-15T18:02:00Z',
      resume_blocked: false,
      actions: ['resume_pause'] },
    expect: 'Started paused at 14:02',
    actions: ['Resume'],
  },
  {
    kind: 'paused_manual',
    item: { id: null, kind: 'paused', level: 'info',
      message: 'Paused at 15:31 — you paused it. Reconciled with Alpaca: 2 positions, all with a stop. Held positions are still reviewed each cycle; no new entries until you resume.',
      since: '2026-10-15T19:31:00Z',
      resume_blocked: false,
      actions: ['resume_pause'] },
    expect: 'Paused at 15:31 — you paused it',
    actions: ['Resume'],
  },
  {
    kind: 'missing_keys',
    item: { id: null, kind: 'missing_keys', level: 'warning',
      message: 'Alpaca API key not set — the agent stays paused until all three keys are set and passing.',
      actions: ['open_settings'] },
    expect: 'stays paused until all three keys',
    actions: ['Settings'],
  },
  {
    kind: 'env_keys',
    item: { id: null, kind: 'env_keys', level: 'info',
      message: 'API keys were imported from .env and now live encrypted in Settings — remove them from .env.',
      actions: [] },
    expect: 'imported from .env',
  },
  {
    kind: 'kill_switch (manual)',
    item: { id: null, kind: 'kill_switch', level: 'critical', automatic: false,
      actions: ['resume', 'flatten'] },
    expect: 'Kill switch is on — no new entries. Open positions stay protected at Alpaca.',
    actions: ['Resume…', 'Stop & flatten'],
  },
  {
    kind: 'kill_switch (automatic)',
    item: { id: null, kind: 'kill_switch', level: 'critical', automatic: true,
      reason: 'drawdown −8.1% from peak',
      actions: ['resume', 'flatten'] },
    expect: 'Trading halted — drawdown −8.1% from peak',
    actions: ['Resume…', 'Stop & flatten'],
  },
  {
    kind: 'shock_brake',
    item: { id: null, kind: 'shock_brake', level: 'warning',
      message: 'Shock brake on: SPY down 2.4% since the open — no new entries this session.',
      actions: [] },
    expect: 'Shock brake on',
  },
  {
    kind: 'daily_loss',
    item: { id: null, kind: 'daily_loss', level: 'warning',
      message: 'Daily loss limit reached — no new entries for the rest of the session.',
      actions: [] },
    expect: 'Daily loss limit reached',
  },
  {
    kind: 'email_failing',
    item: { id: null, kind: 'email_failing', level: 'warning',
      message: 'Email sending failed 3 times in a row — check the SMTP settings.',
      actions: ['open_settings'] },
    expect: 'Email sending failed',
    actions: ['Settings'],
  },
  {
    kind: 'earnings_down',
    item: { id: null, kind: 'earnings_down', level: 'warning',
      message: 'Earnings calendar unavailable. New entries are blocked on every symbol until it is back. Open positions are unaffected.',
      actions: ['connections'] },
    expect: 'Earnings calendar unavailable',
    actions: ['Connections'],
  },
  {
    kind: 'stream_degraded',
    item: { id: null, kind: 'stream_degraded', level: 'critical',
      message: 'Order updates delayed: positions are checked every 15 minutes.',
      actions: [] },
    expect: 'Order updates delayed',
  },
  {
    kind: 'live_decisions_failing',
    item: { id: null, kind: 'live_decisions_failing', level: 'critical',
      message: 'Live decisions failing: openai 401. Held positions are not being reviewed; their stops at Alpaca still stand',
      actions: [] },
    expect: 'Live decisions failing',
  },
  {
    kind: 'eur_unavailable',
    item: { id: null, kind: 'eur_unavailable', level: 'info',
      message: 'EUR unavailable: the ECB rate could not be fetched. Amounts show in USD only until it is back; trading never uses the rate.',
      actions: [] },
    expect: 'EUR unavailable',
  },
  {
    kind: 'market_data_down',
    item: { id: null, kind: 'market_data_down', level: 'warning',
      message: 'No new 5-minute bars for 2 cycles. Entries stop until market data is back; stops at Alpaca still protect open positions.',
      actions: [] },
    expect: 'No new 5-minute bars',
  },
  {
    kind: 'losing_streak',
    item: { id: null, kind: 'losing_streak', level: 'info',
      message: 'Risk per trade cut to 0.25% after 3 losing trades.',
      actions: [] },
    expect: 'Risk per trade cut',
  },
];

for (const c of BANNER_CASES) {
  test(`banner: ${c.kind}`, async ({ page }) => {
    await mockBackground(page);
    await mockBanners(page, [c.item]);
    await page.goto('/');
    // One banner per test: the text is unique to it.
    await expect(page.getByText(c.expect).first()).toBeVisible();
    for (const label of c.actions ?? []) {
      await expect(page.getByRole('button', { name: label })).toBeVisible();
    }
  });
}

// Every Needs you kind attention.py can emit (its _item("kind") calls).
const ATTENTION_ITEMS = [
  { kind: 'stop_missing', severity: 'critical', text: 'KO has no stop at Alpaca since 15:46', detail: '7 shares · broker rejected. Code retries at every check.', action_label: 'Open position', link: '/positions', created_at: '2026-10-15T14:46:00Z' },
  { kind: 'banner_ack', severity: 'critical', text: 'Reconciliation mismatch needs a look.', detail: 'New entries are blocked until you acknowledge.', action_label: 'Acknowledge', link: '/', created_at: '2026-10-15T14:46:00Z' },
  { kind: 'episode', severity: 'critical', text: 'KO exit is held back.', detail: 'Retried before every check; 2 tries so far.', action_label: 'Open position', link: '/positions', created_at: '2026-10-15T14:46:00Z' },
  { kind: 'approval', severity: 'warning', text: 'MSFT entry waits for approval.', detail: 'Supervised mode; expires at the next cycle.', action_label: 'Review', link: '/', created_at: '2026-10-15T14:46:00Z' },
  { kind: 'decision', severity: 'info', text: 'KO earnings exit at the close today.', detail: 'The position leaves at the close — its earnings report lands after the close.', action_label: 'Open Positions', link: '/positions', created_at: null },
  { kind: 'errors', severity: 'warning', text: '3 unresolved errors.', detail: 'Newest: order_rejected.', action_label: 'Open logs', link: '/logs', created_at: '2026-10-15T14:46:00Z' },
  { kind: 'channel', severity: 'warning', text: 'Email sending failed.', detail: '3 tries in a row.', action_label: 'Settings', link: '/settings', created_at: '2026-10-15T14:46:00Z' },
  { kind: 'verdicts', severity: 'info', text: '2 verdicts waiting.', detail: 'Confirm or adjust the suggested verdicts.', action_label: 'Open Reviews', link: '/reviews', created_at: null },
  { kind: 'settings', severity: 'info', text: 'Finnhub key not set.', detail: 'Earnings dates stay unavailable.', action_label: 'Settings', link: '/settings', created_at: null },
];

const HEALTH = {
  alpaca: { ok: true, at: '2026-10-15T14:46:00Z' },
  stream: 'connected',
  clock: { offset_ms: 12, level: 'ok' },
  keys: { status: 'ok', detail: 'all set' },
  email: 'ok',
};

test('Needs you: every kind renders', async ({ page }) => {
  await mockBackground(page);
  await page.route('**/api/attention', (route) =>
    route.fulfill({ json: { items: ATTENTION_ITEMS, count: ATTENTION_ITEMS.length, health: HEALTH } }));
  await page.goto('/');
  const header = page.locator('h2', { hasText: 'Needs you' });
  await expect(header).toBeVisible();
  // The count chip inside the heading shows all nine.
  await expect(header).toContainText(String(ATTENTION_ITEMS.length));
  for (const item of ATTENTION_ITEMS) {
    await expect(page.getByText(item.text, { exact: false }).first()).toBeVisible();
    await expect(page.getByText(item.detail, { exact: false }).first()).toBeVisible();
  }
});

test('Needs you: empty state', async ({ page }) => {
  await mockBackground(page);
  await page.route('**/api/attention', (route) =>
    route.fulfill({ json: { items: [], count: 0, health: HEALTH } }));
  await page.goto('/');
  await expect(page.getByText('Nothing needs you.')).toBeVisible();
});
