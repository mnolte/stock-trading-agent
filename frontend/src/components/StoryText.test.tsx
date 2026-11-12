// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { StoryText } from './StoryText';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

async function render(text: string, glossary?: Record<string, string>) {
  await act(async () => {
    root.render(<StoryText text={text} glossary={glossary} />);
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

describe('StoryText', () => {
  it('renders each paragraph as its own <p>', async () => {
    await render('First paragraph.\n\nSecond paragraph.\n\nThird one.');
    const paragraphs = container.querySelectorAll('p');
    expect(paragraphs).toHaveLength(3);
    expect(paragraphs[0].textContent).toBe('First paragraph.');
    expect(paragraphs[2].textContent).toBe('Third one.');
  });

  it('renders plain text when no glossary is given', async () => {
    await render('The stop held.');
    expect(container.querySelector('button')).toBeNull();
    expect(container.textContent).toContain('The stop held.');
  });

  it('annotates a glossary term as a button with its definition', async () => {
    await render('The stop held through the open.', {
      stop: 'The price at which Alpaca sells automatically.',
    });
    const button = container.querySelector('button.sa-term');
    expect(button).not.toBeNull();
    expect(button?.textContent).toBe('stop');
    expect(button?.getAttribute('title')).toContain('Alpaca');
    // Definition hidden until tapped.
    expect(container.querySelector('[role="tooltip"]')).toBeNull();
    await act(async () => {
      button?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    const tip = container.querySelector('[role="tooltip"]');
    expect(tip?.textContent).toContain('Alpaca sells automatically');
  });

  it('matches whole words, case-insensitively, first occurrence per paragraph', async () => {
    await render('Stop one. stop two.\n\nAnother stop here.', {
      stop: 'definition',
    });
    const buttons = container.querySelectorAll('button.sa-term');
    // One per paragraph: "Stop" in para 1, "stop" in para 2; the second
    // "stop" in para 1 stays plain text.
    expect(buttons).toHaveLength(2);
    expect(container.textContent).toContain('stop two.');
  });

  it('does not match inside a longer word', async () => {
    await render('Nonstop selling continued.', { stop: 'definition' });
    expect(container.querySelector('button.sa-term')).toBeNull();
  });
});
