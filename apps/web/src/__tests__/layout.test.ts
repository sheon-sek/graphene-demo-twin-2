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

  it('keeps every asset exactly where the Plant Design places it', () => {
    let shared = 0;
    const spots = new Set<string>();
    for (const a of design.assets) {
      if (a.room === null || a.x === null || a.y === null) continue;
      const p = layout.assets.get(a.path)!;
      expect([p.x, p.z], a.path).toEqual([a.x, a.y]);
      const key = `${p.floorIndex}:${p.x}:${p.z}`;
      if (spots.has(key)) shared++;
      spots.add(key);
    }
    // Collocated assets stay collocated; click-through picking reaches the one behind.
    expect(shared).toBeGreaterThan(0);
  });

  it('places each authored shaft through the floors it spans', () => {
    const hydraulic = layout.shaftFor('chw')!;
    expect(hydraulic.id).toBe('SH-HYD');
    expect(layout.shaftFor('cw')).toBe(hydraulic);
    expect([hydraulic.x, hydraulic.z]).toEqual([47, 34.5]);
    expect([hydraulic.bottom, hydraulic.top]).toEqual([0, 3]);
    expect(layout.shaftFor('fuel')).toBeUndefined();
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
