import { expect, test } from '@playwright/test';

// Settings pre-fill from "Promote to live" (?promote=<id>), against a mocked
// /promotion response — no seeding endpoint in the app. The save request is
// intercepted too, so the shared e2e settings history is never changed.

const PROMOTION = {
  strategy_id: 42,
  name: 'B · Bigger model',
  allowed: true,
  closed: 20,
  needed: 20,
  changes: { decision: { model: 'gpt-4.1-mini' } },
  lines: ['Model: gpt-4o-mini → gpt-4.1-mini'],
  reason: "Promote shadow 'B · Bigger model' to live",
};

test('promote pre-fills Settings as a new version and saves with the shadow id', async ({
  page,
}) => {
  await page.route('**/api/strategies/42/promotion', (route) =>
    route.fulfill({ json: PROMOTION }),
  );
  let saved: Record<string, unknown> | null = null;
  await page.route('**/api/settings', async (route) => {
    if (route.request().method() === 'POST') {
      saved = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({ json: { version: 99, changes: [], effective_from: 'next cycle' } });
    } else {
      await route.continue();
    }
  });

  await page.goto('/settings?promote=42');
  await expect(
    page.getByText('Promote B · Bigger model to live: Model: gpt-4o-mini → gpt-4.1-mini'),
  ).toBeVisible({ timeout: 60_000 });
  // The difference is filled in as an unsaved change, with a suggested reason.
  await expect(page.locator('select#model')).toHaveValue('gpt-4.1-mini');
  const reason = page.getByLabel('Reason for this change');
  await expect(reason).toHaveValue(PROMOTION.reason);

  // Nothing is promoted until it is saved with a reason.
  expect(saved).toBeNull();
  await page.getByRole('button', { name: /^Save as v/ }).click();
  await expect(page.getByText('B · Bigger model is archived.')).toBeVisible();
  expect(saved).toEqual({
    settings: { decision: { model: 'gpt-4.1-mini' } },
    reason: PROMOTION.reason,
    promote_strategy_id: 42,
  });
  await expect(page).toHaveURL(/\/settings$/);
});

test('promote under 20 closed trades only explains why', async ({ page }) => {
  await page.route('**/api/strategies/42/promotion', (route) =>
    route.fulfill({ json: { ...PROMOTION, allowed: false, closed: 4 } }),
  );
  await page.goto('/settings?promote=42');
  await expect(
    page.getByText("B · Bigger model can't be promoted yet: it needs at least 20 closed trades (has 4)."),
  ).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole('button', { name: /^Save as v/ })).toHaveCount(0);
});
