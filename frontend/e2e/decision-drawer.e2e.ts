import path from 'node:path';
import { expect, type Page, type WebSocketRoute } from '@playwright/test';

import { screenshotDir, test } from './shots';

function detail(id = 9001, version = 'decision.v4', context = 'held') {
  return {
    id, symbol: `TEST${id}`, cycle_time: '2026-10-15T14:46:00Z', context,
    final_action: context === 'held' ? 'hold' : 'skip', no_call_reason: null,
    prompt_version: version, model: 'fake-model', rules_result: null,
    model_output: {
      action: context === 'held' ? 'hold' : 'skip', confidence: 0.4,
      option_id: null, best_option_id: context === 'held' ? null : 'A',
      risks: [], conflicts: version === 'decision.v4' ? [{
        text: 'Momentum is weak and the first resistance level is near the price',
        outweighed_by: 'The daily trend remains up, with enough room to the offered target',
      }] : [], data_flags: [], thesis: 'Reasoning from the stored snapshot.',
      levels_used: [], news_used: [], what_changed: 'Momentum weakened.',
    },
    input_snapshot: {
      price: 105, constraints: { entry_limit: 100, min_room_atr: 1 },
      regime: { trend: 'up', volatility: 'normal' },
      checks: { trend_1h: 'up', trend_daily: 'up', regime_trend: 'up', momentum: 'weak' },
      session: { minutes_left: 74, ...(version === 'decision.v5' ? { last_cycle: false } : {}) },
      position: context === 'held' ? {
        entry: 100, stop: 98, target: 110, r_now: 1, stop_distance_atr: 2,
        target_distance_atr: 3, neutral_probability: 0.4, levels_to_target: [],
        invalidation: 'below 95: initial stop', entry_thesis: 'Both trends up.',
        scheduled_exit: 'earnings exit next session', held_session_hours: 9.5,
        sessions_held: 2, thesis_holds: true,
        checks_at_entry: { trend_1h: 'up', trend_daily: 'up', regime_trend: 'up', momentum: 'strong' },
        ...(version === 'decision.v5' ? { price_vs_signal: -0.48, price_vs_ema20: 'below' } : {}),
      } : null,
      previous_decision: {
        action: 'hold', confidence: 0.4, time: '2026-10-15T13:46:00Z',
        conflicts: [], since: { checks_changed: ['momentum: strong → weak'], price_move_atr: -0.5 },
      },
    },
    validation_result: {
      valid: true, changed_by_code: false, checks: [{
        name: 'Confidence sanity', value: '0.4', detail: 'anchor 0.4', passed: true,
      }],
    },
    gates_result: null, trade: { entry: '100', stop: '98', target: '110', qty: 10, status: 'none' },
    levels: null, options: { options: [{
      id: 'A', stop: 98, target: 110, rr: 5, stop_group: 'S1',
      stop_basis: 'A recorded swing low below the entry price', stop_atr: 2,
      target_basis: 'A recorded swing high above the entry price',
      levels_in_way: [], breakeven_confidence: 0.17,
      ...(version === 'decision.v5' ? { min_confidence: 0.21, target_atr: 10, base_rate: null, base_n: 12 } : {}),
    }] }, chosen_option: null,
    outcome: null, next_outcome_due: null, verdict: null, verdict_eligible: false,
    verdict_due: null, llm_call_id: null, orders: [], approval: null, eur_usd_rate: null,
  };
}

async function mockBackground(page: Page, onSocket?: (socket: WebSocketRoute) => void) {
  await page.routeWebSocket('**/ws', onSocket ?? ((socket) => socket.close()));
  await page.route(/\/api\//, (route) => {
    if (!new URL(route.request().url()).pathname.startsWith('/api/')) return route.fallback();
    if (route.request().url().endsWith('/auth/status'))
      return route.fulfill({ json: { required: false, signed_in: true } });
    return route.fulfill({ status: 503, json: { detail: 'Isolated drawer test' } });
  });
}

async function navigate(page: Page, url: string) {
  await page.evaluate((next) => {
    window.history.pushState({}, '', next);
    window.dispatchEvent(new PopStateEvent('popstate'));
  }, url);
}

for (const version of ['decision.v1', 'decision.v2', 'decision.v3', 'decision.v4', 'decision.v5']) {
  for (const context of ['candidate', 'held']) {
    test(`${version} ${context}: layout and keyboard containment`, async ({ page }, info) => {
      await mockBackground(page);
      await page.route('**/api/decisions/9001', (route) => route.fulfill({ json: detail(9001, version, context) }));
      await page.goto('/decisions/9001');
      const drawer = page.getByRole('dialog');
      await expect(drawer.getByRole('heading', { name: 'TEST9001 decision' })).toBeVisible({ timeout: 60_000 });
      await expect(drawer).toBeFocused();
      await page.keyboard.press('Shift+Tab');
      await expect(drawer.getByRole('link', { name: 'Open position & orders' })).toBeFocused();
      await page.keyboard.press('Tab');
      await expect(drawer.getByRole('button', { name: 'Close', exact: true })).toBeFocused();
      expect(await page.locator('#root').evaluate((node) => (node as HTMLElement).inert)).toBe(true);
      if (context === 'held' && ['decision.v3', 'decision.v4', 'decision.v5'].includes(version)) {
        await expect(drawer.getByRole('heading', { name: 'Position', exact: true })).toBeVisible();
        await expect(drawer.getByText('earnings exit next session')).toBeVisible();
        await expect(drawer.getByText('Neutral probability', { exact: true })).toBeVisible();
      }
      if (['decision.v4', 'decision.v5'].includes(version)) {
        if (context === 'candidate') await expect(drawer.getByText(/Compared with previous cycle:/)).toBeVisible();
        await expect(drawer.getByText('0.4 · anchor 0.4 · pass')).toBeVisible();
        if (context === 'held') await expect(drawer.getByText('confidence · target before current stop')).toBeVisible();
      }
      if (version === 'decision.v5' && context === 'held') {
        await expect(drawer.getByText('Price vs EMA 20')).toBeVisible();
        await expect(drawer.getByText('Price since the signal bar')).toBeVisible();
      }
      if (version === 'decision.v5' && context === 'candidate') {
        await expect(drawer.getByText('Min conf')).toBeVisible();
        await expect(drawer.getByText('Target ATR')).toBeVisible();
      }
      expect(await drawer.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
      const body = drawer.locator('div').filter({ has: page.getByRole('heading', { name: 'Agent reasoning', exact: true }) });
      expect(await body.first().evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
      await page.screenshot({ path: path.join(screenshotDir('decision-drawer'), `${version}-${context}-${info.project.name}.png`) });
      await page.keyboard.press('Escape');
      await expect(drawer).toHaveCount(0);
      expect(await page.locator('#root').evaluate((node) => (node as HTMLElement).inert)).toBe(false);
    });
  }
}

test('focus returns to the opener and verdict failures remain actionable', async ({ page }) => {
  await mockBackground(page);
  const decision = { ...detail(), verdict_eligible: true, verdict: {
    id: 9001, verdict: 'good_call', source: 'draft', reason: 'Stored result',
  } };
  await page.route('**/api/decisions/9001', (route) => route.fulfill({ json: decision }));
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  let fail = true;
  let posts = 0;
  await page.route('**/api/verdicts/9001', async (route) => {
    posts += 1;
    if (fail) {
      await pending;
      return route.fulfill({ status: 422, json: { detail: 'Test failure' } });
    }
    return route.fulfill({ json: {} });
  });
  await page.goto('/');
  await page.evaluate(() => {
    const opener = document.createElement('button');
    opener.id = 'test-opener';
    opener.textContent = 'Open test decision';
    document.body.append(opener);
    opener.focus();
  });
  await navigate(page, '/decisions/9001');
  const drawer = page.getByRole('dialog');
  const pick = drawer.getByRole('button', { name: 'Confirm good call', exact: true });
  await pick.click();
  await expect(pick).toBeDisabled();
  await expect(drawer.getByRole('status')).toHaveText('Saving verdict…');
  release();
  await expect(drawer.getByRole('alert')).toContainText('Retry your choice');
  await expect(pick).toBeEnabled();
  fail = false;
  await pick.click();
  await expect(pick).toBeEnabled();
  expect(posts).toBe(2);
  await expect(drawer.getByRole('alert')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(page.locator('#test-opener')).toBeFocused();
});

test('a verdict finishing after navigation cannot refresh the old decision', async ({ page }) => {
  await mockBackground(page);
  let reads = 0;
  await page.route('**/api/decisions/9001', (route) => {
    reads += 1;
    return route.fulfill({ json: { ...detail(), verdict_eligible: true, verdict: {
      id: 9001, verdict: 'good_call', source: 'draft', reason: 'Stored result',
    } } });
  });
  await page.route('**/api/decisions/9002', (route) => route.fulfill({ json: detail(9002) }));
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await page.route('**/api/verdicts/9001', async (route) => {
    await pending;
    await route.fulfill({ json: {} });
  });
  await page.goto('/decisions/9001');
  await page.getByRole('dialog').getByRole('button', { name: 'Confirm good call', exact: true }).click();
  await navigate(page, '/decisions/9002');
  await expect(page.getByRole('heading', { name: 'TEST9002 decision' })).toBeVisible();
  const before = reads;
  const response = page.waitForResponse('**/api/verdicts/9001');
  release();
  await (await response).finished();
  await page.evaluate(() => new Promise(requestAnimationFrame));
  expect(reads).toBe(before);
  await expect(page.getByRole('heading', { name: 'TEST9002 decision' })).toBeVisible();
});

test('failed load can retry and navigation ignores a late response', async ({ page }) => {
  await mockBackground(page);
  let fail = true;
  await page.route('**/api/decisions/9001', async (route) => {
    if (fail) return route.fulfill({ status: 503, json: {} });
    return route.fulfill({ json: detail() });
  });
  await page.goto('/decisions/9001');
  const retry = page.getByRole('dialog').getByRole('button', { name: 'Retry', exact: true });
  await expect(retry).toBeVisible();
  fail = false;
  await retry.click();
  await expect(page.getByRole('heading', { name: 'TEST9001 decision' })).toBeVisible();
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await page.route('**/api/decisions/9002', async (route) => {
    await pending;
    await route.fulfill({ json: detail(9002) });
  });
  await page.route('**/api/decisions/9003', (route) => route.fulfill({ json: detail(9003) }));
  await navigate(page, '/decisions/9002');
  await expect(page.getByLabel('Loading decision')).toHaveAttribute('aria-busy', 'true');
  await navigate(page, '/decisions/9003');
  await expect(page.getByRole('heading', { name: 'TEST9003 decision' })).toBeVisible();
  const lateResponse = page.waitForResponse('**/api/decisions/9002');
  release();
  await (await lateResponse).finished();
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await expect(page.getByRole('heading', { name: 'TEST9003 decision' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'TEST9002 decision' })).toHaveCount(0);
});

test('price lines remain visible above, below and between levels', async ({ page }) => {
  await mockBackground(page);
  let id = 9100;
  for (const context of ['candidate', 'held']) {
    for (const prices of [[90, 80], [120, 110], [110, 90]]) {
      id += 1;
      const payload = { ...detail(id, 'decision.v4', context), levels: { levels: prices.map((price, index) => ({
        id: `L${index}`, price, kind: 'swing_high', timeframe: '1h', time: null,
      })) } };
      await page.route(`**/api/decisions/${id}`, (route) => route.fulfill({ json: payload }));
      if (id === 9101) await page.goto(`/decisions/${id}`);
      else await navigate(page, `/decisions/${id}`);
      await expect(page.getByRole('heading', { name: `TEST${id} decision` })).toBeVisible();
      const section = page.getByRole('dialog').getByRole('region', { name: 'Levels', exact: true });
      const label = context === 'held' ? 'price' : 'entry limit';
      await expect(section.getByText(label, { exact: true })).toHaveCount(1);
      const content = await section.innerText();
      const anchor = context === 'held' ? 105 : 100;
      await expect(section.getByText(label, { exact: true }).locator('..')).toContainText(anchor.toFixed(2));
      for (const price of prices) {
        expect(content.indexOf(price.toFixed(2)) < content.lastIndexOf(label)).toBe(price >= anchor);
      }
    }
  }
});

test('backtest context never falls through to live decisions or links to live positions', async ({ page }) => {
  await mockBackground(page);
  let liveReads = 0;
  await page.route('**/api/decisions/9001', (route) => {
    liveReads += 1;
    return route.fulfill({ json: detail() });
  });
  await page.goto('/decisions/9001?bt=7');
  const drawer = page.getByRole('dialog');
  await expect(drawer.getByText('The decision link is invalid.')).toBeVisible();
  expect(liveReads).toBe(0);
  await page.route('**/api/backtests/7/strategies/live/decisions/9001', (route) => route.fulfill({ json: detail() }));
  await navigate(page, '/decisions/9001?bt=7&s=live');
  await expect(drawer.getByText('Backtest · no broker order')).toBeVisible();
  await expect(drawer.getByRole('link', { name: 'Back to backtest' })).toHaveAttribute('href', '/backtest');
  await expect(drawer.getByRole('link', { name: 'Open position & orders' })).toHaveCount(0);
  await expect(drawer.getByText('all results in', { exact: true })).toHaveCount(0);
  expect(liveReads).toBe(0);
});

test('live updates refresh the open drawer, preserve focus and recover with stale data', async ({ page }) => {
  const sockets = new Set<WebSocketRoute>();
  await mockBackground(page, (route) => {
    sockets.add(route);
    route.onClose(() => sockets.delete(route));
  });
  const push = (topic: string) => sockets.forEach((socket) =>
    socket.send(JSON.stringify({ type: 'update', topics: [topic] })));
  let thesis = 'Initial stored thesis.';
  let fail = false;
  await page.route('**/api/decisions/9001', (route) => route.fulfill(fail
    ? { status: 503, json: {} }
    : { json: { ...detail(), model_output: { ...detail().model_output, thesis } } }));
  await page.goto('/decisions/9001');
  const drawer = page.getByRole('dialog');
  await expect(drawer.getByText(thesis, { exact: true })).toBeVisible();
  await expect.poll(() => sockets.size).toBeGreaterThan(0);
  await expect(page.getByText('Live updates paused — refreshing every minute.', { exact: true })).toHaveCount(0, { timeout: 15_000 });
  const close = drawer.getByRole('button', { name: 'Close', exact: true }).last();
  await close.focus();
  for (const topic of ['orders', 'reviews', 'decisions']) {
    thesis = `Updated from ${topic}.`;
    push(topic);
    await expect(drawer.getByText(thesis, { exact: true })).toBeVisible();
    await expect(close).toBeFocused();
  }
  fail = true;
  push('positions');
  await expect(drawer.getByRole('alert')).toContainText('Showing stale data');
  await expect(drawer.getByText(thesis, { exact: true })).toBeVisible();
  fail = false;
  await drawer.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(drawer.getByRole('alert')).toHaveCount(0);
  await expect.poll(() => drawer.evaluate((node) => node.contains(document.activeElement))).toBe(true);
});

test('v2 held data, empty stop options and code-blocked actions stay accurate', async ({ page }) => {
  await mockBackground(page);
  const held = detail(9201, 'decision.v2', 'held');
  held.input_snapshot = {
    ...held.input_snapshot,
    position: { entry: 100, stop: 98, target: 110, r_so_far: -0.4 },
  } as typeof held.input_snapshot;
  held.options = { ...held.options, options: [], stop_options: [] };
  await page.route('**/api/decisions/9201', (route) => route.fulfill({ json: held }));
  await page.goto('/decisions/9201');
  const drawer = page.getByRole('dialog');
  await expect(drawer.getByRole('heading', { name: 'TEST9201 decision' })).toBeVisible();
  await expect(drawer.getByText('R now').locator('..')).toContainText('−0.40R');
  await expect(drawer.getByText('the model was not asked')).toHaveCount(0);
  const blocked = detail(9202, 'decision.v2', 'held');
  blocked.validation_result = {
    ...blocked.validation_result!, changed_by_code: true, model_action: 'tighten_stop', final_action: 'hold',
  };
  await page.route('**/api/decisions/9202', (route) => route.fulfill({ json: blocked }));
  await navigate(page, '/decisions/9202');
  await expect(drawer.getByRole('heading', { name: 'TEST9202 decision' })).toBeVisible();
  await expect(drawer.getByText(/10 sh @ 100\.00 · changed by code/)).toBeVisible();
});
