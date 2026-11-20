import { expect, type Page } from '@playwright/test';

import { test } from './shots';

/** Item 109 spacing check (design/DESIGN.md → Spacing (109), kickoff
 * item 13): opens every screen at both viewport sizes and fails when a
 * text block of 7 words or more sits closer than 8 px to a neighbouring
 * text block or to a bordered or filled box in the same column — the
 * vertical gap between siblings in a block or column-flex parent.
 * Table rows, rows with their own padding and inline chips are skipped.
 * Exceptions live in EXCEPTIONS with their reason. */

interface SpacingViolation {
  cls: string;
  text: string;
  sibling: string;
  gap: number;
}

const EXCEPTIONS: { match: string; reason: string }[] = [
  // Filled in as the check finds real exceptions; each needs its reason.
];

async function collectViolations(
  page: Page,
  rootSelector?: string,
): Promise<SpacingViolation[]> {
  const raw = await page.evaluate((sel) => {
    const root: ParentNode = sel ? document.querySelector(sel) ?? document.body : document.body;
    const BLOCK = new Set([
      'ADDRESS', 'ARTICLE', 'ASIDE', 'BLOCKQUOTE', 'DD', 'DETAILS', 'DIALOG',
      'DIV', 'DL', 'DT', 'FIELDSET', 'FIGCAPTION', 'FIGURE', 'FOOTER',
      'FORM', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'HEADER', 'HGROUP', 'HR',
      'LI', 'MAIN', 'NAV', 'OL', 'P', 'PRE', 'SECTION', 'SUMMARY', 'TABLE',
      'UL',
    ]);
    const SKIP_ANCESTOR =
      'table, thead, tbody, tfoot, tr, td, th, select, option, script, style, svg, canvas, noscript, template, [aria-hidden="true"], [class*="skeleton"], [class*="sa-skel"]';
    const isChip = (el: Element) => {
      const c = (el as HTMLElement).className;
      return typeof c === 'string' && /chip/i.test(c);
    };

    const visible = (el: Element): el is HTMLElement => {
      if (!(el instanceof HTMLElement)) return false;
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || cs.visibility === 'collapse')
        return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };

    // Overlays (toasts, the Ask drawer, dialogs) float above the page; they
    // are checked on their own screens, not as neighbours of page content.
    // An element is overlay content when a fixed/absolute ancestor sits
    // between it and the scan root.
    const inOverlay = (el: HTMLElement): boolean => {
      let p = el.parentElement;
      while (p && p !== root) {
        const pos = getComputedStyle(p).position;
        if (pos === 'fixed' || pos === 'absolute') return true;
        p = p.parentElement;
      }
      return false;
    };

    const padY = (el: HTMLElement, side: 'top' | 'bottom') => {
      const cs = getComputedStyle(el);
      const pad = parseFloat(cs.getPropertyValue(`padding-${side}`)) || 0;
      const bw = parseFloat(cs.getPropertyValue(`border-${side}-width`)) || 0;
      const bs = cs.getPropertyValue(`border-${side}-style`);
      const border = bw > 0 && bs !== 'none' && bs !== 'hidden' ? bw : 0;
      return pad + border;
    };

    const wordCount = (el: Element) =>
      (el.textContent ?? '').trim().split(/\s+/).filter(Boolean).length;

    // A leaf text block: no block-level element children, 7+ words.
    const isTextBlock = (el: Element): el is HTMLElement => {
      if (!visible(el)) return false;
      if (inOverlay(el)) return false;
      if (el.closest(SKIP_ANCESTOR)) return false;
      if (isChip(el) || el.closest('[class*="chip"]')) return false;
      for (const child of Array.from(el.children)) {
        if (BLOCK.has(child.tagName)) return false;
      }
      return wordCount(el) >= 7;
    };

    // A bordered or filled box sibling.
    const isBox = (el: Element): el is HTMLElement => {
      if (!visible(el)) return false;
      const cs = getComputedStyle(el);
      if (cs.position === 'absolute' || cs.position === 'fixed') return false;
      if (cs.display === 'inline') return false;
      if (isChip(el)) return false;
      const border = ['top', 'right', 'bottom', 'left'].some((side) => {
        const w = parseFloat(cs.getPropertyValue(`border-${side}-width`));
        const style = cs.getPropertyValue(`border-${side}-style`);
        return w > 0 && style !== 'none' && style !== 'hidden';
      });
      const bg = cs.backgroundColor;
      const filled = bg && bg !== 'transparent' && !bg.endsWith(', 0)');
      return border || Boolean(filled);
    };

    const columnParent = (el: HTMLElement) => {
      const p = el.parentElement;
      if (!p) return false;
      const cs = getComputedStyle(p);
      if (cs.display === 'flex' || cs.display === 'inline-flex')
        return cs.flexDirection.startsWith('column');
      return ['block', 'flow-root', 'list-item', 'grid', 'inline-grid'].includes(cs.display);
    };

    const cls = (el: Element) => {
      const e = el as HTMLElement;
      const id = e.id ? `#${e.id}` : '';
      const c = typeof e.className === 'string' && e.className.trim()
        ? '.' + e.className.trim().split(/\s+/).slice(0, 3).join('.')
        : '';
      return `${el.tagName.toLowerCase()}${id}${c}`;
    };

    const out: {
      cls: string;
      text: string;
      sibling: string;
      gap: number;
    }[] = [];

    const candidates = new Set<HTMLElement>();
    root.querySelectorAll('*').forEach((el) => {
      if (el instanceof HTMLElement && isTextBlock(el)) candidates.add(el);
    });
    // Keep only the outermost candidate: a text block inside another text
    // block is the same words counted twice.
    candidates.forEach((el) => {
      if (el.parentElement && candidates.has(el.parentElement)) candidates.delete(el);
    });

    candidates.forEach((el) => {
      if (!columnParent(el)) return;
      const t = el.getBoundingClientRect();
      const tText = (el.textContent ?? '').trim().replace(/\s+/g, ' ');
      for (const dir of ['previousElementSibling', 'nextElementSibling'] as const) {
        let sib = el[dir] as Element | null;
        // Skip invisible siblings (collapsed spacers, hidden nodes) and
        // overlay content (toasts, drawers) floating above the page.
        while (
          sib &&
          (!visible(sib) ||
            (sib instanceof HTMLElement && inOverlay(sib)))
        )
          sib = sib[dir] as Element | null;
        if (!sib) continue;
        const s = sib.getBoundingClientRect();
        const overlapX = Math.min(t.right, s.right) - Math.max(t.left, s.left);
        if (overlapX <= 0) continue; // not the same column
        const isText = isTextBlock(sib);
        const isFilled = !isText && isBox(sib);
        if (!isText && !isFilled) continue;
        let gap: number;
        if (s.top >= t.bottom) gap = s.top - t.bottom;
        else if (t.top >= s.bottom) gap = t.top - s.bottom;
        else gap = -Math.min(t.bottom - s.top, s.bottom - t.top);
        // Text-to-text: rows with their own padding (list rows, FactRows,
        // watchlist rows) are exempt — measure between the text, not the
        // row boxes. Text-to-box keeps the raw gap: the box edge counts.
        if (isText && el instanceof HTMLElement && sib instanceof HTMLElement) {
          if (s.top >= t.bottom) {
            gap += padY(el, 'bottom') + padY(sib as HTMLElement, 'top');
          } else if (t.top >= s.bottom) {
            gap += padY(el, 'top') + padY(sib as HTMLElement, 'bottom');
          }
        }
        if (gap < 8) {
          out.push({
            cls: cls(el),
            text: tText.slice(0, 80),
            sibling: `${cls(sib)}${isText ? ' (text)' : ''}`,
            gap: Math.round(gap * 10) / 10,
          });
        }
      }
    });
    return out;
  }, rootSelector);
  return raw;
}

function report(screen: string, violations: SpacingViolation[]) {
  const kept = violations.filter(
    (v) => !EXCEPTIONS.some((x) => v.cls.includes(x.match) || v.sibling.includes(x.match)),
  );
  if (kept.length > 0) {
    console.log(
      `[109-spacing] ${screen}:\n` +
        kept.map((v) => `  ${v.cls} "${v.text}" ↔ ${v.sibling} gap ${v.gap}px`).join('\n'),
    );
  }
  return kept;
}

const settle = async (page: Page) => {
  await page.waitForLoadState('networkidle', { timeout: 20_000 }).catch(() => {});
  await page.waitForTimeout(600);
};

const APP_SCREENS: [string, string][] = [
  ['main', '/'],
  ['positions', '/positions'],
  ['performance', '/performance'],
  ['reviews', '/reviews'],
  ['insights', '/insights'],
  ['strategies', '/strategies'],
  ['backtest', '/backtest'],
  ['inspector', '/inspector'],
  ['readiness', '/readiness'],
  ['universe', '/universe'],
  ['logs', '/logs'],
];

const SETTINGS_SECTIONS = [
  'trading', 'risk', 'rules', 'selection', 'gates', 'earnings',
  'model', 'ask', 'notifications', 'display', 'keys', 'access',
  'server', 'readiness', 'schedule', 'history', 'paper-account',
];

test('spacing: app screens', async ({ page }) => {
  const failures: string[] = [];
  for (const [name, path] of APP_SCREENS) {
    await page.goto(path);
    await settle(page);
    for (const v of report(`/${name}`, await collectViolations(page))) {
      failures.push(`${name}: ${v.cls} "${v.text}" ↔ ${v.sibling} gap ${v.gap}px`);
    }
  }
  expect(failures, failures.join('\n')).toEqual([]);
});

test('spacing: settings sections', async ({ page }) => {
  const failures: string[] = [];
  for (const section of SETTINGS_SECTIONS) {
    await page.goto(`/settings/${section}`);
    await settle(page);
    for (const v of report(`settings/${section}`, await collectViolations(page))) {
      failures.push(`settings/${section}: ${v.cls} "${v.text}" ↔ ${v.sibling} gap ${v.gap}px`);
    }
  }
  expect(failures, failures.join('\n')).toEqual([]);
});

test('spacing: ask drawer with an answer', async ({ page }) => {
  // Mock only the Ask endpoints; the rest of the app is the seeded
  // simulated backend, same as the other screens.
  await page.route('**/api/ask/**', (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === '/api/ask/status')
      return route.fulfill({ json: { enabled: true, model: 'fake' } });
    if (path === '/api/ask/threads' && route.request().method() === 'GET')
      return route.fulfill({ json: { threads: [], keep_days: 90 } });
    if (path === '/api/ask/threads' && route.request().method() === 'POST')
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
    if (path === '/api/ask/notes')
      return route.fulfill({ json: { notes: [], count: 0, max: 100 } });
    if (/\/api\/ask\/threads\/1\/messages$/.test(path)) {
      const answer =
        'AAPL closed the week higher on steady volume, and the weekly review flags the energy sleeve as the main drag on the book. ' +
        'The briefing before the open keeps the same two warnings: the stop on MSFT sits inside its earnings window, and the cash buffer is thinner than the rules prefer. ' +
        'Neither needs a decision today; both are worth a look on Monday.';
      const sse = [
        'event: step\ndata: {"tool":"list_positions","label":"Read open positions and their stops","ms":4}\n\n',
        `event: delta\ndata: ${JSON.stringify({ text: answer })}\n\n`,
        `event: done\ndata: ${JSON.stringify({ id: 2, status: 'ok', text: answer, steps: [{ tool: 'list_positions', label: 'Read open positions and their stops' }], references: [], unverified_numbers: [], model: 'fake', lookups: 1, tokens_in: 10, tokens_out: 5, cost: '0.0001', as_of: '2026-10-15T14:00:00Z' })}\n\n`,
      ].join('');
      return route.fulfill({
        status: 200,
        headers: { 'content-type': 'text/event-stream' },
        body: sse,
      });
    }
    return route.fallback();
  });
  await page.goto('/');
  await expect(
    page.getByRole('button', { name: 'Ask the agent' }).first(),
  ).toBeVisible({ timeout: 30_000 });
  await page.keyboard.press('Control+j');
  const drawer = page.getByRole('complementary', { name: 'Ask the agent' });
  await expect(drawer).toBeVisible();
  await page.keyboard.type('How is the week going?');
  await page.keyboard.press('Enter');
  await expect(drawer.getByText(/weekly review flags/)).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(300);
  const failures = report(
    'ask-drawer',
    await collectViolations(page, 'aside.ask-drawer'),
  ).map((v) => `${v.cls} "${v.text}" ↔ ${v.sibling} gap ${v.gap}px`);
  expect(failures, failures.join('\n')).toEqual([]);
});

test('spacing: decision drawer', async ({ page, request }) => {
  const list = await (await request.get('/api/decisions')).json();
  test.skip(!list.items?.length, 'no decisions in the seeded database');
  await page.goto(`/decisions/${list.items[0].id}`);
  await settle(page);
  const dialog = page.getByRole('dialog').first();
  const rootSel = (await dialog.count()) > 0 ? '[role="dialog"]' : undefined;
  const failures = report(
    'decision-drawer',
    await collectViolations(page, rootSel),
  ).map((v) => `${v.cls} "${v.text}" ↔ ${v.sibling} gap ${v.gap}px`);
  expect(failures, failures.join('\n')).toEqual([]);
});
