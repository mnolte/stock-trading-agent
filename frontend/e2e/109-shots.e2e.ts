import path from 'node:path';
import type { Page } from '@playwright/test';

import { expect } from '@playwright/test';
import { screenshotDir, test } from './shots';

// Item 109 screenshots for docs/screenshots/109/ (pass SCREENSHOT_DIR or
// E2E_SHOTS=1 to write them for real). The desktop and mobile projects run
// every test at both widths; the suffix names the PNG.
const outDir = screenshotDir('109');

function shotPath(name: string, project: string, suffix?: string) {
  const s = suffix ?? (project === 'mobile' ? '390' : '1440');
  return path.join(outDir, `${name}-${s}.png`);
}

test('insights: model costs, this month and all time', async ({ page }, testInfo) => {
  await page.goto('/insights');
  const section = page.locator('#q6');
  await expect(section.getByText('What do the models cost?')).toBeVisible({
    timeout: 60_000,
  });
  await page.screenshot({
    path: shotPath('insights-costs-this-month', testInfo.project.name),
    fullPage: false,
  });
  await section.getByRole('button', { name: 'All time' }).click();
  await expect(page).toHaveURL(/costs=all/);
  await page.screenshot({
    path: shotPath('insights-costs-all', testInfo.project.name),
    fullPage: false,
  });
});

test('settings: the model page with both folds open', async ({
  page,
}, testInfo) => {
  await page.goto('/settings/model');
  await expect(
    page.getByRole('heading', { name: 'Model and reviews' }),
  ).toBeVisible({ timeout: 60_000 });
  const wordLimits = page.locator('summary', { hasText: 'Word limits' });
  await wordLimits.click();
  await wordLimits.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: shotPath('settings-model', testInfo.project.name),
    fullPage: false,
  });
  if (testInfo.project.name === 'desktop') {
    await page.setViewportSize({ width: 1024, height: 800 });
    await page.screenshot({
      path: shotPath('settings-model', testInfo.project.name, '1024'),
      fullPage: false,
    });
    await page.setViewportSize({ width: 1440, height: 900 });
  }
  // The Ask limits fold lives on the Ask settings page.
  await page.goto('/settings/ask');
  const askLimits = page.locator('summary', { hasText: 'Limits per answer' });
  await askLimits.click();
  await askLimits.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: shotPath('settings-ask', testInfo.project.name),
    fullPage: false,
  });
});

test('reviews and readiness', async ({ page }, testInfo) => {
  await page.goto('/reviews');
  await expect(
    page.getByRole('heading', { name: 'Reviews' }),
  ).toBeVisible({ timeout: 60_000 });
  await page.screenshot({
    path: shotPath('reviews', testInfo.project.name),
    fullPage: false,
  });
  await page.goto('/readiness');
  await expect(
    page.getByRole('heading', { name: 'Readiness' }),
  ).toBeVisible({ timeout: 60_000 });
  await page.screenshot({
    path: shotPath('readiness', testInfo.project.name),
    fullPage: false,
  });
});

test('backtests', async ({ page }, testInfo) => {
  await page.goto('/backtests');
  await expect(
    page.getByRole('heading', { name: 'Backtest' }),
  ).toBeVisible({ timeout: 60_000 });
  await page.screenshot({
    path: shotPath('backtest', testInfo.project.name),
    fullPage: false,
  });
});

/** The Ask drawer is mocked like ask-drawer.e2e.ts: the seeded app's Ask is
 * not configured, so the API answers are faked and every other call passes
 * through to the real e2e backend. */
const ASK_THREAD = {
  id: 1,
  title: 'Cost of NVDA?',
  context: { screen: 'dashboard', label: 'Overview' },
  questions: 2,
  created_at: '2026-10-12T15:00:00Z',
  updated_at: '2026-10-12T15:05:00Z',
};

const ASK_MESSAGES = [
  {
    id: 11,
    role: 'user',
    text: 'What did the decision cost?',
    status: 'ok',
    created_at: '2026-10-12T15:00:00Z',
  },
  {
    id: 12,
    role: 'assistant',
    text: 'The last decision cost **$0.0021** across two model calls: one to decide, one to write the review.\n\nCached prompt tokens covered most of the input, so the run was cheaper than the list price suggests.',
    status: 'ok',
    cost: '0.0021',
    created_at: '2026-10-12T15:01:00Z',
  },
];

const ASK_NOTE = {
  id: 5,
  text: 'Stops are broker-side: every position carries one and the agent never holds a position naked.',
  references: [],
  source_thread_id: 1,
  source_message_id: 12,
  source_chat: 'Cost of NVDA?',
  created_at: '2026-10-10T09:00:00Z',
  updated_at: '2026-10-10T09:00:00Z',
};

const SSE_ANSWER = [
  'event: delta\ndata: {"text":"The last week cost **$0.018** across 9 calls."}\n\n',
  'event: done\ndata: {"id":13,"status":"ok","text":"The last week cost $0.018 across 9 calls.","cost":"0.0018","model":"fake"}\n\n',
].join('');

async function mockAsk(page: Page, capped = false) {
  await page.route('**/api/ask/**', (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    if (p === '/api/ask/status')
      return route.fulfill({
        json: capped
          ? {
              enabled: false,
              reason: 'The monthly cost cap is reached ($16.00 of $15.00).',
              spent_usd: '16.00',
              cap_usd: 15,
              questions: 3,
              questions_cost_usd: '0.0126',
              avg_cost_usd: '0.0042',
            }
          : {
              enabled: true,
              model: 'gpt-6-luna',
              context_tokens: 60000,
              questions: 2,
              questions_cost_usd: '0.0042',
              avg_cost_usd: '0.0021',
              spent_usd: '0.02',
              cap_usd: 15,
            },
      });
    if (p === '/api/ask/threads' && route.request().method() === 'GET')
      return route.fulfill({ json: { threads: [ASK_THREAD], keep_days: 90 } });
    if (p === '/api/ask/threads' && route.request().method() === 'POST')
      return route.fulfill({ json: ASK_THREAD });
    if (p === '/api/ask/threads/1')
      return route.fulfill({ json: { ...ASK_THREAD, messages: ASK_MESSAGES } });
    if (p === '/api/ask/threads/1/messages')
      return route.fulfill({
        status: 200,
        headers: { 'content-type': 'text/event-stream' },
        body: SSE_ANSWER,
      });
    if (p === '/api/ask/notes')
      return route.fulfill({ json: { notes: [ASK_NOTE], count: 1, max: 100 } });
    return route.fulfill({ status: 404, json: { detail: 'not mocked' } });
  });
}

test('ask drawer: thread, notes, mid-answer and at the cap', async ({
  page,
}, testInfo) => {
  await mockAsk(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible({
    timeout: 60_000,
  });
  await page.keyboard.press('Control+j');
  const drawer = page.getByRole('complementary', { name: 'Ask the agent' });
  await expect(drawer).toBeVisible({ timeout: 60_000 });
  await page.screenshot({
    path: shotPath('ask-first-open', testInfo.project.name),
    fullPage: false,
  });

  await page.getByRole('button', { name: 'Earlier chats' }).click();
  await page.getByRole('tab', { name: /Notes/ }).click();
  await page.screenshot({
    path: shotPath('ask-notes', testInfo.project.name),
    fullPage: false,
  });

  await page.getByRole('tab', { name: /Chats/ }).click();
  await page.locator('button.ask-thread-open', { hasText: ASK_THREAD.title }).click();
  await expect(drawer.getByText('What did the decision cost?')).toBeVisible();
  await page.screenshot({
    path: shotPath('ask-thread', testInfo.project.name),
    fullPage: false,
  });

  await drawer.getByLabel('Your question').fill('And the week before?');
  await drawer.getByRole('button', { name: 'Send', exact: true }).click();
  await page.waitForTimeout(400);
  await page.screenshot({
    path: shotPath('ask-mid-answer', testInfo.project.name),
    fullPage: false,
  });
});

test('ask drawer: at the monthly cap', async ({ page }, testInfo) => {
  await mockAsk(page, true);
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible({
    timeout: 60_000,
  });
  await page.keyboard.press('Control+j');
  const drawer = page.getByRole('complementary', { name: 'Ask the agent' });
  await expect(drawer).toBeVisible({ timeout: 60_000 });
  await expect(drawer.getByText(/monthly cost cap/i)).toBeVisible();
  await page.screenshot({
    path: shotPath('ask-cap', testInfo.project.name),
    fullPage: false,
  });
});
