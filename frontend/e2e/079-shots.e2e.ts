import path from 'node:path';

import { expect, type Page } from '@playwright/test';

import { screenshotDir, test } from './shots';

// 079 — PR screenshots (docs/screenshots/079/): the dashboard briefing
// card and the Reviews weekly story as v3 prose with glossary terms and
// "The numbers", plus the code-written template after a failed format
// check. Run with SCREENSHOT_DIR=../docs/screenshots/079; a plain
// `make test` writes to a temp folder instead.

const outDir = screenshotDir('079');

const STORY =
  'The account pressed its stops all week and finished higher. ' +
  'Each closed trade risked one R, and the sample is still small.\n\n' +
  'No position is open into the weekend; the next briefing reads the ' +
  'overnight tape before the open.';

const GLOSSARY = {
  stop: 'The price at which Alpaca sells a position automatically, to cap the loss.',
  R: 'What a trade risks: entry minus stop, times the shares.',
  sample: 'How much evidence the result rests on; with few trades one can still swing it either way.',
};

const weeklyFacts = {
  period: { week: 42, start: '2026-10-12', end: '2026-10-16', label: '12 – 16 Oct' },
  limits: { weekly_summary_words: 120, weekly_story_words: 350, verdict_after_sessions: 5, skip_flat_pct: 2 },
  glossary: GLOSSARY,
  return_pct: 1.49,
  return_usd: 149,
  equity_start: 10000,
  equity_end: 10149,
  spy_return_pct: 0.8,
  vs_spy_pts: 0.69,
  max_drawdown_pct: -0.9,
  trades_closed: 3,
  won: 2,
  lost: 1,
  avg_r: 0.41,
  model_cost_usd: 0.02,
  model_calls: 12,
  reviews_cost_usd: 0.001,
  trades: [],
  best_trade: null,
  worst_trade: null,
  skips: { total: 0, with_outcome: 0, pending: 0, up: 0, flat: 0, down: 0, biggest_miss: null },
  verdicts: { good_call: 0, bad_call: 0, unclear: 0, total: 0 },
  suggestion_allowed: false,
  by_regime: [],
  by_symbol: [],
};

function weeklyDetail(over: Record<string, unknown> = {}) {
  return {
    id: 9001,
    kind: 'weekly',
    period: '2026-W42',
    label: 'Week 42',
    sub: '12 – 16 Oct',
    source: 'model',
    reason: null,
    due_at: '2026-10-17T08:00:00+00:00',
    created_at: '2026-10-17T08:00:04+00:00',
    model: 'gpt-4.1-mini',
    prompt_version: 'weekly.v3',
    facts: weeklyFacts,
    text: STORY,
    content: { story: STORY, suggestion: null },
    status_line: null,
    tokens: null,
    cost: '0.0004',
    ...over,
  };
}

const weeklyItem = {
  id: 9001,
  kind: 'weekly',
  period: '2026-W42',
  label: 'Week 42',
  sub: '12 – 16 Oct',
  source: 'model',
  reason: null,
  due_at: '2026-10-17T08:00:00+00:00',
  created_at: '2026-10-17T08:00:04+00:00',
  model: 'gpt-4.1-mini',
  prompt_version: 'weekly.v3',
  return_pct: 1.49,
};

/** Mock the reviews list + detail; every other call hits the real app. */
async function mockWeekly(page: Page, detail: Record<string, unknown>) {
  await page.route(/\/api\/reviews/, (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/reviews') {
      return route.fulfill({
        json: {
          items: [weeklyItem],
          next_weekly_due: null,
          schedule: 'Weekly review Saturday 10:00 · Daily briefing 09:00 New York',
        },
      });
    }
    return route.fulfill({ json: detail });
  });
  await page.route(/\/api\/verdicts/, (route) =>
    route.fulfill({ json: { items: [] } }),
  );
}

test('dashboard briefing card: the story, terms and The numbers', async ({
  page,
}, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';
  await page.goto('/');
  const card = page.locator('section[aria-label="Daily briefing"]');
  await expect(card).toBeVisible({ timeout: 60_000 });
  // The v3 story paragraphs and the fold, not the old one-block text.
  await expect(card.getByText('The numbers', { exact: false })).toBeVisible({
    timeout: 30_000,
  });
  await card.screenshot({
    path: path.join(outDir, `dashboard-briefing-${suffix}.png`),
  });
});

test('reviews weekly: the v3 story with annotated terms', async ({
  page,
}, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';
  await mockWeekly(page, weeklyDetail());
  await page.goto('/reviews');
  await expect(
    page.getByRole('heading', { name: /Week \d+ ·/ }),
  ).toBeVisible({ timeout: 60_000 });
  await expect(page.locator('button.sa-term').first()).toBeVisible();
  await page.screenshot({
    path: path.join(outDir, `reviews-weekly-${suffix}.png`),
    fullPage: true,
  });
});

test('a glossary term opens its definition on tap (phone)', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'phone popover shot');
  await mockWeekly(page, weeklyDetail());
  await page.goto('/reviews');
  const term = page.locator('button.sa-term').first();
  await expect(term).toBeVisible({ timeout: 60_000 });
  await term.click();
  await expect(page.locator('[role="tooltip"]').first()).toBeVisible();
  await page.screenshot({
    path: path.join(outDir, 'term-popover-390.png'),
    fullPage: true,
  });
});

test('template fallback after a failed format check', async ({
  page,
}, testInfo) => {
  const suffix = testInfo.project.name === 'mobile' ? '390' : '1440';
  await mockWeekly(
    page,
    weeklyDetail({
      source: 'template',
      reason: 'format check failed',
      model: null,
      content: {
        story:
          'Week 42 (12 – 16 Oct) closed with the account +1.49% ' +
          '(+$149.00), against SPY +0.80% (+0.69 pts vs SPY). ' +
          'The deepest dip was -0.90%.\n\n' +
          '3 trades closed: 2 won, 1 lost, averaging +0.41R per trade.\n\n' +
          'So far: 3 closed trades in the run so far. That is still a ' +
          'small sample — one trade can swing it either way.',
        suggestion: null,
      },
    }),
  );
  await page.goto('/reviews');
  await expect(
    page.getByText('Plain review: format check failed'),
  ).toBeVisible({ timeout: 60_000 });
  await page.screenshot({
    path: path.join(outDir, `template-fallback-${suffix}.png`),
    fullPage: true,
  });
});
