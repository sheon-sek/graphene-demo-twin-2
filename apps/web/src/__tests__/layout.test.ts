import { describe, expect, it } from 'vitest';
import { buildLayout, floorElevation, STOREY_M } from '../lib/layout';
import { placedPaths, plantDesign } from './fixtures';

const design = plantDesign();

describe('floor stack', () => {
  it('stacks Ground, L1, L2 and Roof one storey apart when collapsed', () => {
    expect([0, 1, 2, 3].map((i) => floorElevation(i, 0))).toEqual([
      0,
      STOREY_M,
      2 * STOREY_M,
      3 * STOREY_M,
    ]);
  });

  it('separates the floors when exploded', () => {
    for (let i = 1; i < 4; i++) {
      expect(floorElevation(i, 1) - floorElevation(i - 1, 1)).toBeGreaterThan(2 * STOREY_M);
    }
    expect(floorElevation(0, 1)).toBe(0);
    expect(floorElevation(2, 0.5)).toBeGreaterThan(floorElevation(2, 0));
    expect(floorElevation(2, 0.5)).toBeLessThan(floorElevation(2, 1));
  });
});

describe('layout', () => {
  const layout = buildLayout(design);

  it('places every placed asset on its floor, inside its room footprint', () => {
    for (const path of placedPaths(design)) {
      const p = layout.assets.get(path)!;
      expect(p, path).toBeDefined();
      const room = layout.rooms.get(p.room)!;
      expect(p.floorIndex).toBe(room.floorIndex);
      expect(p.x, path).toBeGreaterThanOrEqual(room.x);
      expect(p.x, path).toBeLessThanOrEqual(room.x + room.w);
      expect(p.z, path).toBeGreaterThanOrEqual(room.z);
      expect(p.z, path).toBeLessThanOrEqual(room.z + room.d);
    }
  });

  it('never stacks two assets on the same spot', () => {
    const spots = new Set<string>();
    for (const path of placedPaths(design)) {
      const p = layout.assets.get(path)!;
      const key = `${p.floorIndex}:${p.x.toFixed(2)}:${p.z.toFixed(2)}`;
      expect(spots.has(key), path).toBe(false);
      spots.add(key);
    }
  });

  it('leaves Support Assets out of the building', () => {
    const support = design.assets.filter((a) => a.room === null);
    expect(support.length).toBeGreaterThan(0);
    for (const a of support) expect(layout.assets.has(a.path)).toBe(false);
  });

  it('knows the site extents', () => {
    expect(layout.bounds.minX).toBe(0);
    expect(layout.bounds.maxX).toBe(136);
    expect(layout.bounds.maxZ).toBe(60);
  });
});
