import { describe, expect, it } from 'vitest';
import { buildLayout } from '../lib/layout';
import { anchorOf, LAYERS, layerOf, RISERS, routeConnection, type Vec3 } from '../lib/routing';
import type { ConnectionKind } from '../lib/types';
import { plantDesign } from './fixtures';

const design = plantDesign();
const layout = buildLayout(design);
const EPS = 1e-9;

function axesChanged(a: Vec3, b: Vec3): number {
  return [0, 1, 2].filter((i) => Math.abs(a[i] - b[i]) > EPS).length;
}

describe('layers', () => {
  it('groups every connection kind into one of the five layers', () => {
    const kinds: ConnectionKind[] = ['power', 'chw', 'cw', 'air', 'water', 'net', 'fuel'];
    const used = new Set(kinds.map(layerOf));
    expect([...used].sort()).toEqual([...LAYERS].sort());
    expect(LAYERS).toEqual(['Electrical', 'Hydraulic', 'Airside', 'Network', 'Water']);
    expect(layerOf('chw')).toBe('Hydraulic');
    expect(layerOf('cw')).toBe('Hydraulic');
    expect(layerOf('power')).toBe('Electrical');
  });
});

describe('orthogonal routing', () => {
  for (const explode of [0, 1]) {
    it(`routes all ${design.connections.length} connections with axis-aligned runs (explode ${explode})`, () => {
      for (const c of design.connections) {
        const route = routeConnection(c, layout, explode);
        expect(route.length, `${c.source} → ${c.target}`).toBeGreaterThanOrEqual(2);
        expect(route[0]).toEqual(anchorOf(c.source, layout, explode));
        expect(route[route.length - 1]).toEqual(anchorOf(c.target, layout, explode));
        for (let i = 1; i < route.length; i++) {
          expect(axesChanged(route[i - 1], route[i]), `${c.source} → ${c.target}`).toBe(1);
        }
      }
    });
  }

  it('changes floor only inside the layer riser', () => {
    const between = design.connections.filter(
      (c) => layout.floorOf(c.source) !== layout.floorOf(c.target),
    );
    expect(between.length).toBeGreaterThan(0);
    for (const c of between) {
      const route = routeConnection(c, layout, 1);
      const riser = RISERS[layerOf(c.kind)];
      const floors = new Set<number>();
      for (let i = 1; i < route.length; i++) {
        const [a, b] = [route[i - 1], route[i]];
        const vertical = Math.abs(a[1] - b[1]) > EPS;
        const long = Math.abs(a[1] - b[1]) > 6;
        if (vertical && long) {
          expect([a[0], a[2]], `${c.source} → ${c.target}`).toEqual([riser.x, riser.z]);
          floors.add(i);
        }
      }
      expect(floors.size, `${c.source} → ${c.target}`).toBe(1);
    }
  });

  it('keeps same-floor runs on that floor', () => {
    const c = design.connections.find(
      (c) => c.source === 'Chiller/R_CP1' && c.target === 'Chiller/R_C1',
    )!;
    const route = routeConnection(c, layout, 1);
    const ys = route.map((p) => p[1]);
    expect(Math.max(...ys) - Math.min(...ys)).toBeLessThan(6);
    expect(route.every((p) => p[0] < 40 && p[2] >= 18)).toBe(true);
  });

  it('ends airside connections in the room they serve', () => {
    const air = design.connections.find((c) => c.kind === 'air' && layout.rooms.has(c.target))!;
    const room = layout.rooms.get(air.target)!;
    const end = anchorOf(air.target, layout, 0);
    expect(end[0]).toBeCloseTo(room.x + room.w / 2);
    expect(end[2]).toBeCloseTo(room.z + room.d / 2);
  });
});
