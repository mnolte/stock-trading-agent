import { expect, test, type Page } from '@playwright/test';

// 056 step 2 — error text is one line outside Logs, and toasts stack below
// the header so they never cover Pause / Resume or the kill switch
// (docs/analysis/056-ux-review.md finding 2).

function notificationItem(id: number, subject: string) {
  return {
    id,
    time: new Date().toISOString(),
    event: 'new_error',
    severity: 'critical',
    subject,
    body: 'Reviews screen failed to draw ×5 · last 07:30',
    link: '/logs',
    channels: ['app'],
    channel_status: { app: 'sent', browser: 'skipped', email: 'skipped' },
    error: null,
  };
}

/** Three persistent (critical) toasts; the GET seeds empty, then the
 * WS wake-up refetch returns all three. */
async function mockThreeToasts(page: Page) {
  const items = [1, 2, 3].map((i) =>
    notificationItem(900_000 + i, `[Stock Trading Agent · PAPER] Error ${i}`),
  );
  let seeded = false;
  await page.route('**/api/notifications**', (route) => {
    if (!seeded) {
      seeded = true;
      return route.fulfill({
        contentType: 'application/json',
        json: { items: [], latest_id: items[0].id - 1 },
      });
    }
    return route.fulfill({
      contentType: 'application/json',
      json: { items, latest_id: items[2].id },
    });
  });
}

test('toasts sit below the header and never cover Pause or Kill switch', async ({
  page,
  request,
}) => {
  await mockThreeToasts(page);
  await page.clock.install();
  await page.goto('/');
  await expect(page.locator('.sa-skel').first()).toBeHidden({
    timeout: 60_000,
  });
  await request.post('/api/notifications/test');

  const region = page.getByRole('region', { name: 'Notifications' });
  await expect(async () => {
    await page.clock.runFor(500);
    expect(await region.getByRole('alert').count()).toBe(3);
  }).toPass({ timeout: 30_000, intervals: [200] });

  // The toast stack starts below the header's bottom edge.
  const header = page.locator('header').first();
  const headerBox = await header.boundingBox();
  const toastBox = await region.boundingBox();
  expect(headerBox).not.toBeNull();
  expect(toastBox).not.toBeNull();
  expect(toastBox!.y).toBeGreaterThanOrEqual(headerBox!.y + headerBox!.height);

  // Header controls are the topmost element at their own centre — a toast
  // covering them would win the hit test instead.
  for (const pattern of [/Pause|Resume/, /Kill switch|Trading halted/]) {
    const button = page.getByRole('button', { name: pattern }).first();
    await expect(button).toBeVisible();
    const box = await button.boundingBox();
    expect(box).not.toBeNull();
    const hit = await page.evaluate(
      ([x, y]) => document.elementFromPoint(x, y)?.textContent ?? '',
      [box!.x + box!.width / 2, box!.y + box!.height / 2],
    );
    expect(hit).toMatch(pattern);
  }
});
