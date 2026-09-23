import { describe, expect, it, vi } from 'vitest';
import { LiveStore } from '../lib/live';
import { sparklinePath } from '../lib/sparkline';
import type { Frame } from '../lib/types';

function frame(seq: number, points: Frame['points'], epoch = 0): Frame {
  return {
    seq,
    epoch,
    time: 1_790_000_000 + seq,
    timestamp: new Date((1_790_000_000 + seq) * 1000).toISOString(),
    events: 0,
    points,
  };
}

const good = (value: number | boolean | string) => ({ value, quality: 'good' as const });

describe('live store over SSE', () => {
  it('takes every reading from a snapshot and changes from a delta', () => {
    const live = new LiveStore(4);
    live.apply('snapshot', frame(1, { a: good(1), b: good('x') }));
    live.apply('delta', frame(2, { a: good(2) }));
    expect(live.reading('a')).toEqual(good(2));
    expect(live.reading('b')).toEqual(good('x'));
    expect(live.time).toBe(1_790_000_002);
    expect(live.seq).toBe(2);
  });

  it('keeps a bounded history per point, one sample per frame, for sparklines', () => {
    const live = new LiveStore(3);
    live.apply('snapshot', frame(1, { a: good(1), flag: good(false), s: good('x') }));
    live.apply('delta', frame(2, { a: good(5) }));
    live.apply('delta', frame(3, { flag: good(true) }));
    live.apply('delta', frame(4, { a: good(7) }));
    expect(live.history('a')).toEqual([5, 5, 7]);
    expect(live.history('flag')).toEqual([0, 1, 1]);
    expect(live.history('s').every(Number.isNaN)).toBe(true);
    expect(live.history('missing')).toEqual([]);
  });

  it('drops everything on a new snapshot (Reset starts a new epoch)', () => {
    const live = new LiveStore(4);
    live.apply('snapshot', frame(1, { a: good(1), gone: good(2) }));
    live.apply('delta', frame(2, { a: good(3) }));
    live.apply('snapshot', frame(0, { a: good(9) }, 1));
    expect(live.epoch).toBe(1);
    expect(live.reading('gone')).toBeUndefined();
    expect(live.history('a')).toEqual([9]);
  });

  it('records no sample for a frame that only logs an event at the same second', () => {
    const live = new LiveStore(4);
    live.apply('snapshot', frame(1, { a: good(1) }));
    live.apply('delta', { ...frame(2, {}), time: live.time, events: 1 });
    expect(live.eventCount).toBe(1);
    expect(live.history('a')).toEqual([1]);
    live.apply('delta', { ...frame(3, { a: good(4) }), events: 1 });
    expect(live.history('a')).toEqual([1, 4]);
  });

  it('tells subscribers once per frame', () => {
    const live = new LiveStore(4);
    const listener = vi.fn();
    const stop = live.subscribe(listener);
    live.apply('snapshot', frame(1, { a: good(1) }));
    live.apply('delta', frame(2, {}));
    stop();
    live.apply('delta', frame(3, {}));
    expect(listener).toHaveBeenCalledTimes(2);
    expect(live.version).toBe(3);
  });
});

describe('sparkline', () => {
  it('scales samples into the box, oldest on the left', () => {
    expect(sparklinePath([0, 10], 100, 20)).toBe('M0,20L100,0');
  });

  it('draws a flat series through the middle and skips gaps', () => {
    expect(sparklinePath([3, 3, 3], 10, 20)).toBe('M0,10L5,10L10,10');
    expect(sparklinePath([0, Number.NaN, 10], 10, 20)).toBe('M0,20M10,0');
    expect(sparklinePath([], 10, 20)).toBe('');
  });
});

describe('active faults over SSE', () => {
  it('takes the full fault list from every frame', () => {
    const live = new LiveStore(4);
    const fault = {
      key: 'crac.compressor_trip@CRAC/L1_CRAC3',
      fault: 'crac.compressor_trip',
      name: 'Compressor trip',
      category: 'equipment' as const,
      target: 'CRAC/L1_CRAC3',
      severity: 1,
      level: 1,
      since: 1_790_000_000,
      rampMin: 0,
      until: null,
    };
    live.apply('snapshot', { ...frame(1, {}), faults: [fault] });
    expect(live.faults).toEqual([fault]);
    expect(live.faultTargets).toBe('CRAC/L1_CRAC3');
    live.apply('delta', { ...frame(2, {}), faults: [] });
    expect(live.faults).toEqual([]);
    expect(live.faultTargets).toBe('');
  });
});
