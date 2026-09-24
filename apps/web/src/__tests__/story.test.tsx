import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { StoryControls, formatOffset } from '../components/StoryControls';
import { LiveStore } from '../lib/live';
import type { EventLogDoc } from '../lib/types';

const demo: EventLogDoc & { durationS: number } = {
  format: 'graphene-demo-twin/event-log',
  version: 1,
  title: 'Golden Demo: a grid failure',
  description: 'Utility loss, gensets, one UPS on battery.',
  durationS: 180,
  events: [
    {
      offset: 60,
      kind: 'fault.inject',
      target: 'Meter/SPPA Incomer 1',
      params: { fault: 'utility.incomer_loss' },
      note: 'Grid failure on side A.',
    },
    {
      offset: 180,
      kind: 'fault.inject',
      target: 'UPS/UPS 1',
      params: { fault: 'ups.rectifier_failure' },
      note: 'One UPS on battery.',
    },
  ],
};

function mockFetch(routes: Record<string, (body: unknown) => unknown>) {
  const calls: { method: string; url: string; body: unknown }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(init.body as string) : undefined;
      calls.push({ method, url, body });
      const route = routes[`${method} ${url}`];
      if (!route) return new Response('{}', { status: 404 });
      return new Response(JSON.stringify(route(body)), { status: 200 });
    }),
  );
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('Golden Demo controls', () => {
  it('plays the Golden Demo only after the Reset is confirmed, then follows the story', async () => {
    const calls = mockFetch({
      'GET /api/golden-demo': () => demo,
      'POST /api/golden-demo/play': () => ({
        title: demo.title,
        start: 1000,
        timestamp: '',
        durationS: 180,
        events: [],
      }),
    });
    const live = new LiveStore(4);
    render(<StoryControls live={live} onError={(e) => expect.fail(String(e))} />);
    fireEvent.click(screen.getByRole('button', { name: 'Play Golden Demo…' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Confirm play' });
    expect(dialog.textContent).toContain(demo.title);
    expect(dialog.textContent).toContain('resets the Live World');
    expect(calls.some((c) => c.method === 'POST')).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: 'Reset and play' }));
    await waitFor(() => expect(screen.getByRole('list', { name: 'Story' })).toBeTruthy());
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ reset: true, confirm: true });
    expect(screen.getByText('Grid failure on side A.')).toBeTruthy();
  });

  it('formats story offsets as minutes and seconds', () => {
    expect(formatOffset(0)).toBe('0:00');
    expect(formatOffset(425)).toBe('7:05');
  });
});
