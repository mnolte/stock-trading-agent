import { expect, type Page, type WebSocketRoute } from '@playwright/test';

import { test } from './shots';

async function mockBackground(page: Page, onSocket?: (socket: WebSocketRoute) => void) {
  await page.routeWebSocket('**/ws', onSocket ?? ((socket) => socket.close()));
  await page.route(/\/api\//, (route) => {
    const url = new URL(route.request().url());
    // Vite serves modules under /src/api/ — only true API paths get mocked.
    if (!url.pathname.startsWith('/api/')) return route.fallback();
    if (url.pathname === '/api/auth/status')
      return route.fulfill({ json: { required: false, signed_in: true } });
    if (url.pathname === '/api/ask/status')
      return route.fulfill({ json: { enabled: true, model: 'fake' } });
    if (url.pathname === '/api/ask/threads' && route.request().method() === 'GET')
      return route.fulfill({ json: { threads: [], keep_days: 90 } });
    if (url.pathname === '/api/ask/threads' && route.request().method() === 'POST')
      return route.fulfill({
        json: {
          id: 1,
          title: 'New chat',
          context: null,
          created_at: '2026-10-15T14:00:00Z',
          updated_at: '2026-10-15T14:00:00Z',
          questions: 0,
        },
      });
    if (url.pathname === '/api/ask/notes')
      return route.fulfill({ json: { notes: [], count: 0, max: 100 } });
    if (/\/api\/ask\/threads\/1\/messages$/.test(url.pathname)) {
      const sse = [
        'event: step\ndata: {"tool":"list_positions","label":"Read open positions and their stops","ms":4}\n\n',
        'event: delta\ndata: {"text":"No open positions."}\n\n',
        'event: done\ndata: {"id":2,"status":"ok","text":"No open positions.","steps":[{"tool":"list_positions","label":"Read open positions and their stops"}],"references":["position:MSFT"],"unverified_numbers":[],"model":"fake","lookups":1,"tokens_in":10,"tokens_out":5,"cost":"0.0001","as_of":"2026-10-15T14:00:00Z"}\n\n',
      ].join('');
      return route.fulfill({
        status: 200,
        headers: { 'content-type': 'text/event-stream' },
        body: sse,
      });
    }
    return route.fulfill({ status: 503, json: { detail: 'Isolated drawer test' } });
  });
}

test('Ctrl+J opens the drawer on Positions and a question streams', async ({ page }) => {
  await mockBackground(page);
  await page.goto('/');
  // Wait for the app shell before the shortcut — the keydown listener
  // is attached by React on mount, after goto resolves.
  await expect(
    page.getByRole('button', { name: 'Ask the agent' }).first(),
  ).toBeVisible({ timeout: 30_000 });
  await page.keyboard.press('Control+j');
  const drawer = page.getByRole('complementary', { name: 'Ask the agent' });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByPlaceholder(/Ask about this page/i)).toBeFocused();

  await page.keyboard.type('How much do I lose if every stop hits?');
  await page.keyboard.press('Enter');
  await expect(drawer.getByText('Looked up 1 thing')).toBeVisible();
  await drawer.getByText('Looked up 1 thing').click();
  await expect(drawer.getByText('Read open positions and their stops')).toBeVisible();
  await expect(drawer.getByText('No open positions.')).toBeVisible();
  await expect(drawer.getByRole('link', { name: 'MSFT position' })).toBeVisible();

  await page.keyboard.press('Escape');
  await expect(drawer).not.toBeVisible();
});

test('phone width fills the screen without sideways scroll', async ({ page }) => {
  await mockBackground(page);
  await page.setViewportSize({ width: 390, height: 780 });
  await page.goto('/');
  const askBtn = page.getByRole('button', { name: 'Ask the agent' }).first();
  await expect(askBtn).toBeVisible({ timeout: 30_000 });
  await askBtn.click();
  const drawer = page.getByRole('complementary', { name: 'Ask the agent' });
  await expect(drawer).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
});
