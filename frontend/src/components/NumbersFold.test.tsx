// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { NumbersFold } from './NumbersFold';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

async function render(rows: { label: string; value: string }[], factCount?: number) {
  await act(async () => {
    root.render(<NumbersFold rows={rows} factCount={factCount} />);
  });
}

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe('NumbersFold', () => {
  it('renders nothing when there are no rows', async () => {
    await render([]);
    expect(container.textContent).toBe('');
  });

  it('lists label–value rows inside a details fold', async () => {
    await render(
      [
        { label: 'Equity', value: '$10,340.00' },
        { label: 'Return', value: '+3.40%' },
      ],
      9,
    );
    const details = container.querySelector('details');
    expect(details).not.toBeNull();
    expect(details?.querySelector('summary')?.textContent).toContain(
      'The numbers',
    );
    expect(details?.querySelector('summary')?.textContent).toContain(
      '9 facts the story was given',
    );
    expect(container.textContent).toContain('Equity');
    expect(container.textContent).toContain('$10,340.00');
    expect(container.textContent).toContain('+3.40%');
  });
});
