import path from 'node:path';

import { expect, type Page } from '@playwright/test';

import { screenshotDir, test } from './shots';

// 107 — dashboard cards line up (design/screens/Main.dc.html). Every row
// of top-level cards starts and ends at the page column's edges with
// 20 px between cards, and the three two-card rows share one 3 : 2
// split from 1200 px. The empty jump-target spans of item 073 are gone:
// each id sits on its card, so a span put back inside a row adds a
// child and fails the checks below.

const outDir = screenshotDir('107');

const WIDTHS = [1440, 1024, 390] as const;

interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

async function settle(page: Page) {
  await expect(page.locator('main')).toBeVisible({ timeout: 60_000 });
  await page
    .locator('.sa-skel')
    .first()
    .waitFor({ state: 'hidden', timeout: 30_000 })
    .catch(() => {});
  await page.waitForTimeout(400);
}

/** The dashboard's page column: the flex column that holds the card
 * rows (no padding, so its border box is the content box). */
function pageColumn(page: Page) {
  return page.locator('.sa-dash-pair').first().locator('xpath=..');
}

async function boxes(locator: ReturnType<Page['locator']>): Promise<Box[]> {
  return locator.evaluateAll((els) =>
    els.map((el) => {
      const r = el.getBoundingClientRect();
      return { x: r.x, y: r.y, width: r.width, height: r.height };
    }),
  );
}

function expectClose(actual: number, expected: number, what: string) {
  expect(Math.abs(actual - expected), `${what}: ${actual} vs ${expected}`).toBeLessThanOrEqual(1);
}

/** First card's left edge and last card's right edge on the column's
 * edges; every gap between consecutive cards 20 px (horizontal when the
 * cards share a line, vertical when stacked). */
async function expectRowAligned(page: Page, row: ReturnType<Page['locator']>, column: Box) {
  const items = await boxes(row.locator('> *'));
  expect(items.length).toBeGreaterThanOrEqual(1);
  const left = Math.min(...items.map((b) => b.x));
  const right = Math.max(...items.map((b) => b.x + b.width));
  expectClose(left, column.x, 'row left edge');
  expectClose(right, column.x + column.width, 'row right edge');
  const sorted = [...items].sort((a, b) => a.y - b.y || a.x - b.x);
  for (let i = 1; i < sorted.length; i++) {
    const prev = sorted[i - 1];
    const cur = sorted[i];
    const sameLine =
      cur.y < prev.y + prev.height && prev.y < cur.y + cur.height;
    const gap = sameLine ? cur.x - (prev.x + prev.width) : cur.y - (prev.y + prev.height);
    expectClose(gap, 20, `gap between card ${i} and ${i + 1}`);
  }
}

test('dashboard card rows line up', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'widths are set inside the test');
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    await settle(page);

    const columnBox = await pageColumn(page).boundingBox();
    expect(columnBox).not.toBeNull();
    const column: Box = {
      x: columnBox!.x,
      y: columnBox!.y,
      width: columnBox!.width,
      height: columnBox!.height,
    };

    const pairs = page.locator('.sa-dash-pair');
    await expect(pairs).toHaveCount(3);
    for (let i = 0; i < 3; i++) {
      await expectRowAligned(page, pairs.nth(i), column);
    }

    // The single-card rows reach the same edges.
    for (const label of ['Watchlist', 'Account summary']) {
      const card = page.locator(`main section[aria-label="${label}"]`).first();
      await expect(card).toBeVisible();
      const b = await card.boundingBox();
      expectClose(b!.x, column.x, `${label} left edge`);
      expectClose(b!.x + b!.width, column.x + column.width, `${label} right edge`);
    }

    if (width === 1440) {
      // One shared split: the three two-card rows end their left card
      // at the same x.
      const splits: number[] = [];
      for (let i = 0; i < 3; i++) {
        const items = await boxes(pairs.nth(i).locator('> *'));
        const sorted = [...items].sort((a, b) => a.x - b.x);
        splits.push(sorted[0].x + sorted[0].width);
      }
      for (const s of splits.slice(1)) {
        expectClose(s, splits[0], 'two-card row split');
      }
    }
  }
});

test('dashboard screenshots', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'widths are set inside the test');
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    await settle(page);
    await page.screenshot({
      path: path.join(outDir, `dashboard-${width}.png`),
      fullPage: true,
    });
  }
});

// Minimal insights snapshot in the shape compute_insights writes —
// enough for the five question blocks to render.
const INSIGHTS = {
  strategies: [{ id: 'live', name: 'Live', kind: 'llm' }],
  period: 'run',
  empty: false,
  snapshot: {
    session_date: '2026-12-18',
    computed_at: '2026-12-18T21:05:00',
    trades: 112,
    answers: {
      edge: 'Probably, but not proven yet.',
      stands_out: '1 finding.',
      trades_lost: 'The risk gates cost the most entries.',
      conditions: 'Entry cycle is the best so far.',
      costs: 'Overnight holds earned less than the session.',
    },
    headline: {
      expectancy: { avg_r: '0.21', lo: '0.02', hi: '0.40', n: 112, needs: 10, have: 112 },
      edge_over_spy: { pts: '1.70', return_pct: '3.20', spy_return_pct: '4.00', exposure: '0.38', needs: 10, have: 112 },
      beta: { beta: '0.31', correlation: '0.55', sessions: 58, needs: 3, have: 58 },
      model_cost: { total_usd: '14.80', per_trade_usd: '0.13', pct_gross_profit: '2.10', needs: 10, have: 112 },
    },
    funnel: {
      steps: [
        { key: 'symbol_checks', count: 24708, losses: [] },
        { key: 'rules', count: 3410, losses: [{ label: 'rules', count: 21298 }] },
        { key: 'called', count: 2060, losses: [{ label: 'call cap', count: 1180 }] },
        { key: 'proposed', count: 214, losses: [{ label: 'model skipped', count: 1846 }] },
        { key: 'code_checks', count: 205, losses: [] },
        { key: 'risk_gates', count: 140, losses: [{ label: 'sector cap', count: 21 }] },
        { key: 'filled', count: 116, losses: [{ label: 'unfilled limits', count: 24 }] },
        { key: 'closed', count: 112, losses: [{ label: 'still open', count: 4 }] },
      ],
    },
    findings: [],
    skipped: {
      total: 1846, scored: 1840, missed: 350, avoided: 626, neither: 864,
      missed_pct: '19.00', avoided_pct: '34.00', neither_pct: '47.00',
      baseline_r: '-0.11', not_filled: 6, na: null,
    },
    slices: {
      entry_cycle: { label: 'Entry cycle', hidden: 0, rows: [] },
    },
    filters_removed: [],
    calibration: { buckets: null, weighted_gap_pts: null, needs: 50, have: 12, hidden: 0 },
    execution: {
      fill_rate: '80.00', filled: 4, placed: 5, worst_cycle: null,
      entry_slippage_bps: '2.40', exit_slippage_bps: '3.10', exit_slippage_splits: [],
      gaps_count: 0, gaps_extra_r: null,
      same_day_exits: 1, same_day_avg_r: '-0.40',
      earnings_exits: 0, earnings_avg_r: null, earnings_median_move_pct: null,
      dividends_usd: '0.00', dividend_positions: 0,
    },
    overnight: {
      in_session_usd: '38.00', overnight_usd: '-12.00',
      nights: 6, gapped_against: 2, gapped_pct: '33.33', largest_gap: null,
    },
  },
};

test('insights question headings keep the column gap', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'desktop layout');
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.route('**/api/insights?**', (route) =>
    route.fulfill({ contentType: 'application/json', json: INSIGHTS }),
  );
  await page.goto('/insights');
  await expect(page.locator('#q5')).toBeVisible({ timeout: 60_000 });

  // The gap above each question heading equals the gap between the
  // column's other blocks: one flex gap everywhere, no empty anchor
  // element taking a second gap (item 107).
  const rows = await page.evaluate(() => {
    const q1 = document.getElementById('q1');
    const col = q1?.parentElement;
    if (!col) return [];
    const isQuestion = (el: Element) => /^q[1-5]$/.test(el.id);
    return Array.from(col.children)
      .filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      })
      .map((el, i, all) => {
        const r = el.getBoundingClientRect();
        const prev = all[i - 1];
        const prevBottom = prev
          ? prev.getBoundingClientRect().bottom
          : null;
        return {
          question: isQuestion(el),
          gapAbove: prevBottom === null ? null : r.top - prevBottom,
        };
      });
  });
  expect(rows.length).toBeGreaterThan(5);
  const questionGaps = rows.filter((r) => r.question).map((r) => r.gapAbove);
  const otherGaps = rows
    .filter((r) => !r.question && r.gapAbove !== null)
    .map((r) => r.gapAbove as number);
  expect(questionGaps).toHaveLength(5);
  for (const gap of questionGaps) {
    expect(gap).not.toBeNull();
    for (const other of otherGaps) {
      expectClose(gap as number, other, 'question heading gap');
    }
  }
});
