// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CardBoundary } from './CardBoundary';
import { resetCardErrorReports } from './cardErrorReports';
import { RiskBudgetCard } from './RiskBudgetCard';
import type { Resource } from '../api/useResource';
import type { RiskBudgetResponse } from '../types/api';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

function Boom(): never {
  throw new Error('facts.trades is not iterable');
}

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let fetchMock: ReturnType<typeof vi.fn>;

async function render(ui: ReactNode) {
  await act(async () => {
    root.render(<MemoryRouter>{ui}</MemoryRouter>);
  });
}

beforeEach(() => {
  resetCardErrorReports();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ error_id: 'e-123' }),
  });
  vi.stubGlobal('fetch', fetchMock);
  // React logs caught errors to console.error — silence it here.
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('CardBoundary', () => {
  it('shows the failed-to-draw state and keeps siblings rendered', async () => {
    await render(
      <>
        <CardBoundary name="Weekly review">
          <Boom />
        </CardBoundary>
        <div data-testid="sibling">sibling ok</div>
      </>,
    );
    expect(container.textContent).toContain('couldn’t be drawn');
    expect(container.textContent).toContain('The agent isn’t affected');
    expect(container.textContent).toContain('sibling ok');
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
  });

  it('reports the error once per card and page load across reloads', async () => {
    await render(
      <CardBoundary name="Weekly review">
        <Boom />
      </CardBoundary>,
    );
    // The report resolves to an error id shown in the fallback.
    await act(async () => {});
    expect(container.textContent).toContain('Error e-123');
    const reports = () =>
      fetchMock.mock.calls.filter(
        ([url]) => url === '/api/logs/client',
      ).length;
    expect(reports()).toBe(1);

    // Reload card remounts the card; it throws again — no new report.
    for (let i = 0; i < 3; i++) {
      const reload = [...container.querySelectorAll('button')].find(
        (b) => b.textContent === 'Reload card',
      );
      expect(reload).toBeDefined();
      await act(async () => {
        reload!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      });
      await act(async () => {});
      expect(container.textContent).toContain('couldn’t be drawn');
    }
    expect(reports()).toBe(1);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/logs/client',
      expect.objectContaining({
        body: expect.stringContaining('react card boundary (Weekly review)'),
      }),
    );
  });

  it('a malformed risk-budget payload fails only its own card', async () => {
    // Regression (056 follow-up): the card header read sectors[0] before
    // the boundary, so a bad payload took the whole screen down.
    const res = {
      data: {},
      loaded: true,
      updatedAt: new Date(),
      loading: false,
      showSkeleton: false,
      slow: false,
      slowSeconds: 0,
      refreshing: false,
      failure: null,
      reload: () => {},
    } as unknown as Resource<RiskBudgetResponse>;
    await render(
      <>
        <RiskBudgetCard res={res} />
        <div data-testid="sibling">sibling ok</div>
      </>,
    );
    expect(container.textContent).toContain('couldn’t be drawn');
    expect(container.textContent).toContain('sibling ok');
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
  });
});
