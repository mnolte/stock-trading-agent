import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

import { SavedValues } from './SavedValues';

const item = {
  key: 'concurrency.max_requests', label: 'Requests at once', saved: 10, config: 20,
  since_version: 12, since_at: '2026-10-21T12:00:00Z', direction: 'looser',
};

describe('saved values without a control', () => {
  it('shows the saved and config values and their first version', () => {
    const html = renderToStaticMarkup(<SavedValues items={[item]} onUse={vi.fn()} />);
    expect(html).toContain('Saved values without a control');
    expect(html).toContain('Requests at once');
    expect(html).toContain('concurrency.max_requests');
    expect(html).toContain('>10<');
    expect(html).toContain('>20<');
    expect(html).toContain('v12');
    expect(html).toContain('Use the config value');
  });

  it('hides the entire block when every saved value is represented or equal', () => {
    expect(renderToStaticMarkup(<SavedValues items={[]} onUse={vi.fn()} />)).toBe('');
  });
});
