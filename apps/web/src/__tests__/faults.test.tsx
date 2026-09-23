import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BottomBar } from '../components/BottomBar';
import { Inspector } from '../components/Inspector';
import { LiveStore } from '../lib/live';
import type { ActiveFault, FaultPreview, FaultSpec, Frame, PointInfo } from '../lib/types';
import { buildWorld } from '../lib/world';
import { initialConsoleState, useConsole } from '../store';
import { plantDesign } from './fixtures';

const CRAC3 = 'CRAC/L1_CRAC3';
const SENSOR = 'Temperature and Humidity/Datahall 3/Sensor 17';

const design = plantDesign();
const assetPaths = design.assets.filter((a) => !a.unexported).map((a) => a.path);
const points: PointInfo[] = [
  { path: `${CRAC3}/Supply Air Temperature`, sourceClass: 'process_value' },
  { path: `${CRAC3}/System Failure_Trip`, sourceClass: 'fault_alarm' },
  { path: 'CRAC/L1_CRAC1/System Failure_Trip', sourceClass: 'fault_alarm' },
  { path: `${SENSOR}/Temp`, sourceClass: 'process_value' },
];
const spec = (id: string, name: string, assetType: string, category: FaultSpec['category']) => ({
  id,
  name,
  assetType,
  category,
  mechanism: 'physical_constraint',
  variable: 'constraint.x',
  span: 1,
  unit: '',
  description: `${name} described`,
  defaultSeverity: 1,
});
const catalog: FaultSpec[] = [
  spec('crac.compressor_trip', 'Compressor trip', 'CRAC', 'equipment'),
  spec('crac.setpoint_drift', 'Setpoint drift', 'CRAC', 'control'),
  spec('th.drift', 'Sensor drift', 'Temperature and Humidity', 'sensor'),
];
const world = buildWorld(design, assetPaths, points, catalog);

const trip: ActiveFault = {
  key: `crac.compressor_trip@${CRAC3}`,
  fault: 'crac.compressor_trip',
  name: 'Compressor trip',
  category: 'equipment',
  target: CRAC3,
  severity: 1,
  level: 0.5,
  since: 1_790_000_000,
  rampMin: 0,
  until: null,
};

function frame(seq: number, faults: ActiveFault[], alarm = false, events = 0): Frame {
  return {
    seq,
    epoch: 0,
    time: 1_790_000_000 + seq,
    timestamp: '2026-09-21T14:13:20Z',
    events,
    points: {
      [`${CRAC3}/System Failure_Trip`]: { value: alarm, quality: 'good' },
      'CRAC/L1_CRAC1/System Failure_Trip': { value: false, quality: 'good' },
    },
    faults,
  };
}

const preview: FaultPreview = {
  target: CRAC3,
  fault: 'crac.compressor_trip',
  params: { severity: 1, ramp_min: 0, auto_clear_min: null },
  minutes: 15,
  start: 1_790_000_000,
  end: 1_790_000_900,
  timestamp: '2026-09-21T14:28:20Z',
  affected: [
    { node: CRAC3, firstAt: 1_790_000_001, afterS: 1, hops: 0 },
    { node: 'DH03', firstAt: 1_790_000_001, afterS: 1, hops: 1 },
    { node: SENSOR, firstAt: 1_790_000_001, afterS: 1, hops: 2 },
  ],
  diffs: [
    {
      path: `${SENSOR}/Temp`,
      node: SENSOR,
      base: 24.01,
      predicted: 28.5,
      baseQuality: 'good',
      predictedQuality: 'good',
    },
  ],
  alarms: [
    {
      path: `${CRAC3}/System Failure_Trip`,
      node: CRAC3,
      base: false,
      predicted: true,
      firstAt: 1_790_000_010,
      afterS: 10,
    },
  ],
};

interface Call {
  method: string;
  url: string;
  body: unknown;
}

/** Stub fetch with `METHOD url` routes; returns the calls made. */
function mockFetch(routes: Record<string, (body: unknown) => unknown>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(init.body as string) : undefined;
      calls.push({ method, url, body });
      const route = routes[`${method} ${url}`];
      if (!route) return new Response(JSON.stringify({ detail: 'not found' }), { status: 404 });
      const out = route(body);
      return out instanceof Response ? out : new Response(JSON.stringify(out), { status: 200 });
    }),
  );
  return calls;
}

const posts = (calls: Call[], url: string) =>
  calls.filter((c) => c.method === 'POST' && c.url === url).map((c) => c.body);

beforeEach(() => useConsole.setState({ ...initialConsoleState, expanded: new Set() }));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('inspector, Faults tab', () => {
  function open(live = new LiveStore(4)) {
    render(<Inspector world={world} live={live} />);
    act(() => useConsole.getState().select(CRAC3));
    fireEvent.click(screen.getByRole('tab', { name: 'Faults' }));
    return live;
  }

  it('offers the catalog for the selected asset type only', () => {
    open();
    expect(screen.getByRole('radio', { name: /Compressor trip/ })).toBeTruthy();
    expect(screen.getByRole('radio', { name: /Setpoint drift/ })).toBeTruthy();
    expect(screen.queryByRole('radio', { name: /Sensor drift/ })).toBeNull();
  });

  it('previews the fault on the selected asset and shows the propagation', async () => {
    const calls = mockFetch({ 'POST /api/faults/preview': () => preview });
    open();
    fireEvent.click(screen.getByRole('radio', { name: /Compressor trip/ }));
    fireEvent.change(screen.getByRole('slider', { name: 'Severity' }), {
      target: { value: '0.6' },
    });
    fireEvent.click(screen.getByRole('radio', { name: 'Ramp' }));
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Ramp minutes' }), {
      target: { value: '5' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Preview 15 min' }));

    const result = await screen.findByRole('region', { name: 'Fault Preview' });
    expect(posts(calls, '/api/faults/preview')).toEqual([
      {
        target: CRAC3,
        fault: 'crac.compressor_trip',
        params: { severity: 0.6, ramp_min: 5, auto_clear_min: null },
        minutes: 15,
      },
    ]);
    const affected = within(result).getByRole('list', { name: 'Affected assets' });
    expect(within(affected).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      expect.stringContaining('L1_CRAC3'),
      expect.stringContaining('DH03'),
      expect.stringContaining('Sensor 17'),
    ]);
    const alarms = within(result).getByRole('list', { name: 'Alarm changes' });
    expect(alarms.textContent).toContain('System Failure_Trip');
    expect(alarms.textContent).toContain('false → true');
    const diffs = within(result).getByRole('table', { name: 'Point changes' });
    expect(diffs.textContent).toContain('24.01');
    expect(diffs.textContent).toContain('28.5');

    fireEvent.click(within(affected).getByRole('button', { name: /DH03/ }));
    expect(useConsole.getState().selected).toBe(CRAC3); // rooms are not selectable
    fireEvent.click(within(affected).getByRole('button', { name: /Sensor 17/ }));
    expect(useConsole.getState().selected).toBe(SENSOR);
  });

  it('injects on exactly the selected asset (never the first of its type)', async () => {
    const calls = mockFetch({ 'POST /api/faults': (body) => ({ ...(body as object), at: 1 }) });
    open();
    fireEvent.click(screen.getByRole('radio', { name: /Setpoint drift/ }));
    fireEvent.click(screen.getByRole('radio', { name: 'Auto-clear' }));
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Auto-clear minutes' }), {
      target: { value: '20' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Inject' }));
    await waitFor(() => expect(posts(calls, '/api/faults')).toHaveLength(1));
    expect(posts(calls, '/api/faults')[0]).toEqual({
      target: CRAC3,
      fault: 'crac.setpoint_drift',
      params: { severity: 1, ramp_min: 0, auto_clear_min: 20 },
    });
  });

  it("shows the twin's reason when it rejects an injection", async () => {
    mockFetch({
      'POST /api/faults': () =>
        new Response(JSON.stringify({ detail: 'crac.compressor_trip is already active' }), {
          status: 409,
        }),
    });
    open();
    fireEvent.click(screen.getByRole('radio', { name: /Compressor trip/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Inject' }));
    expect((await screen.findByRole('alert')).textContent).toContain('already active');
  });

  it('lists the faults active on this asset, each with its own Clear', async () => {
    const calls = mockFetch({ 'POST /api/faults/clear': (body) => body });
    const live = open();
    act(() => live.apply('snapshot', frame(1, [trip])));
    const active = screen.getByRole('region', { name: 'Active faults on this asset' });
    expect(active.textContent).toContain('Compressor trip');
    expect(active.textContent).toContain('50 %');
    fireEvent.click(within(active).getByRole('button', { name: 'Clear Compressor trip' }));
    await waitFor(() =>
      expect(posts(calls, '/api/faults/clear')).toEqual([
        { target: CRAC3, fault: 'crac.compressor_trip' },
      ]),
    );
  });
});

describe('inspector, Control tab', () => {
  const commands = (mode: string) => ({
    target: CRAC3,
    commands: [
      {
        name: 'mode',
        label: 'Hand / auto',
        kind: 'choice',
        choices: ['auto', 'hand'],
        minimum: null,
        maximum: null,
        unit: '',
        value: mode,
      },
      {
        name: 'run',
        label: 'Start / stop (hand)',
        kind: 'switch',
        choices: [],
        minimum: null,
        maximum: null,
        unit: '',
        value: true,
      },
      {
        name: 'setpoint',
        label: 'Supply air setpoint',
        kind: 'number',
        choices: [],
        minimum: 14,
        maximum: 28,
        unit: '°C',
        value: 18,
      },
    ],
  });

  it('gives Operator Commands on the selected asset', async () => {
    const calls = mockFetch({
      [`GET /api/commands/CRAC/L1_CRAC3`]: () => commands('auto'),
      'POST /api/commands': (body) => ({ ...(body as object), kind: 'command' }),
    });
    render(<Inspector world={world} live={new LiveStore(4)} />);
    act(() => useConsole.getState().select(CRAC3));
    fireEvent.click(screen.getByRole('tab', { name: 'Control' }));

    const mode = await screen.findByRole('group', { name: 'Hand / auto' });
    expect(within(mode).getByRole('button', { name: 'auto' }).getAttribute('aria-pressed')).toBe(
      'true',
    );
    const run = screen.getByRole('group', { name: 'Start / stop (hand)' });
    expect(within(run).getByRole('button', { name: 'Stop' })).toHaveProperty('disabled', true);

    fireEvent.click(within(mode).getByRole('button', { name: 'hand' }));
    const setpoint = screen.getByRole('group', { name: 'Supply air setpoint' });
    fireEvent.change(within(setpoint).getByRole('spinbutton'), { target: { value: '16.5' } });
    fireEvent.click(within(setpoint).getByRole('button', { name: 'Set' }));
    await waitFor(() => expect(posts(calls, '/api/commands')).toHaveLength(2));
    expect(posts(calls, '/api/commands')).toEqual([
      { target: CRAC3, command: 'mode', value: 'hand' },
      { target: CRAC3, command: 'setpoint', value: 16.5 },
    ]);
  });
});

describe('bottom bar', () => {
  const events = {
    time: 1_790_000_005,
    events: [
      {
        at: 1_790_000_002,
        timestamp: '2026-09-21T14:13:22Z',
        kind: 'fault.inject',
        target: CRAC3,
        params: { fault: 'crac.compressor_trip', severity: 1 },
      },
    ],
  };

  it('lists active faults with a Clear each, and the alarm bits that are set', async () => {
    const calls = mockFetch({
      'GET /api/events': () => events,
      'POST /api/faults/clear': (body) => body,
    });
    const live = new LiveStore(4);
    live.apply('snapshot', frame(1, [trip], true, 1));
    render(<BottomBar world={world} live={live} />);

    const faults = screen.getByRole('region', { name: 'Active faults' });
    expect(faults.textContent).toContain('Compressor trip');
    fireEvent.click(within(faults).getByRole('button', { name: `Clear Compressor trip on ${CRAC3}` }));
    await waitFor(() =>
      expect(posts(calls, '/api/faults/clear')).toEqual([
        { target: CRAC3, fault: 'crac.compressor_trip' },
      ]),
    );

    const alarms = screen.getByRole('region', { name: 'Alarms' });
    expect(within(alarms).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      expect.stringContaining('System Failure_Trip'),
    ]);
    fireEvent.click(within(alarms).getByRole('button'));
    expect(useConsole.getState().selected).toBe(CRAC3);

    const log = screen.getByRole('region', { name: 'Event Log' });
    await waitFor(() => expect(log.textContent).toContain('fault.inject'));
    expect(log.textContent).toContain('14:13:22');
    expect(log.textContent).toContain('crac.compressor_trip');
  });

  it('resets only after an in-page confirmation', async () => {
    const calls = mockFetch({
      'GET /api/events': () => events,
      'POST /api/reset': () => ({ epoch: 1 }),
    });
    const live = new LiveStore(4);
    live.apply('snapshot', frame(1, [], false, 1));
    render(<BottomBar world={world} live={live} />);

    fireEvent.click(screen.getByRole('button', { name: 'Reset…' }));
    expect(posts(calls, '/api/reset')).toEqual([]);
    expect(screen.getByRole('alertdialog').textContent).toContain('1 event');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('alertdialog')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Reset…' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm reset' }));
    await waitFor(() => expect(posts(calls, '/api/reset')).toEqual([{ confirm: true }]));
  });
});
