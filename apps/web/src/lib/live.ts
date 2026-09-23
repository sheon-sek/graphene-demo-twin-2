import type { ActiveFault, Frame, Reading, Value } from './types';

/**
 * The Live World as the console sees it over `/api/stream`: the latest reading of every point
 * and a short per-point history for sparklines, one sample per frame. A snapshot (the first
 * event, and the first after a Reset) replaces everything.
 */
export class LiveStore {
  seq = -1;
  epoch = -1;
  time = 0;
  timestamp = '';
  eventCount = 0;
  /** Active faults in injection order. */
  faults: ActiveFault[] = [];
  /** The assets with an active fault, one per line: a cheap value to subscribe to. */
  faultTargets = '';
  /** Bumped once per applied frame; lets React subscribe cheaply. */
  version = 0;

  private readings = new Map<string, Reading>();
  private rings = new Map<string, Float64Array>();
  /** Number of frames recorded since the last snapshot. */
  private frames = 0;
  private listeners = new Set<() => void>();

  constructor(readonly capacity = 120) {}

  apply(kind: 'snapshot' | 'delta', frame: Frame): void {
    if (kind === 'snapshot') {
      this.readings = new Map(Object.entries(frame.points));
      this.rings = new Map();
      this.frames = 0;
    } else {
      for (const [path, reading] of Object.entries(frame.points)) this.readings.set(path, reading);
    }
    const slot = this.frames % this.capacity;
    for (const [path, reading] of this.readings) {
      let ring = this.rings.get(path);
      if (!ring) {
        ring = new Float64Array(this.capacity).fill(Number.NaN);
        this.rings.set(path, ring);
      }
      ring[slot] = numeric(reading.value);
    }
    this.frames++;
    this.seq = frame.seq;
    this.epoch = frame.epoch;
    this.time = frame.time;
    this.timestamp = frame.timestamp;
    this.eventCount = frame.events;
    this.faults = frame.faults ?? [];
    this.faultTargets = [...new Set(this.faults.map((f) => f.target))].join('\n');
    this.version++;
    for (const listener of this.listeners) listener();
  }

  reading(path: string): Reading | undefined {
    return this.readings.get(path);
  }

  /** Samples oldest first; NaN where a value is not numeric. */
  history(path: string): number[] {
    const ring = this.rings.get(path);
    if (!ring) return [];
    const n = Math.min(this.frames, this.capacity);
    const start = this.frames - n;
    return Array.from({ length: n }, (_, i) => ring[(start + i) % this.capacity]);
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
}

function numeric(value: Value): number {
  if (typeof value === 'number') return value;
  if (typeof value === 'boolean') return value ? 1 : 0;
  return Number.NaN;
}
