import { describe, expect, it } from 'vitest';
import { connectionsAround, neighbours } from '../lib/graph';
import { plantDesign } from './fixtures';

const design = plantDesign();
const edge = (i: number) => design.connections[i];

describe('upstream and downstream of a selection', () => {
  it('lists the direct neighbours by connection kind, in authored order', () => {
    const { upstream, downstream } = neighbours(design, 'Chiller/R_C1');
    expect(upstream.chw).toContain('Chiller/R_CP1');
    expect(upstream.cw).toContain('Chiller/R_CP5');
    expect(Object.keys(downstream).length).toBeGreaterThan(0);
  });

  it('highlights whole chains, following one connection kind at a time', () => {
    const { upstream, downstream } = connectionsAround(design, 'Chiller/R_C1');
    expect(upstream.length).toBeGreaterThan(0);
    expect(downstream.length).toBeGreaterThan(0);
    // The chain runs along the edges: each highlighted upstream edge feeds the chiller or another
    // highlighted upstream edge of the same kind.
    for (const i of upstream) {
      const e = edge(i);
      const feeds =
        e.target === 'Chiller/R_C1' ||
        upstream.some((j) => edge(j).source === e.target && edge(j).kind === e.kind);
      expect(feeds, `${e.source} → ${e.target}`).toBe(true);
    }
    for (const i of downstream) {
      const e = edge(i);
      const fed =
        e.source === 'Chiller/R_C1' ||
        downstream.some((j) => edge(j).target === e.source && edge(j).kind === e.kind);
      expect(fed, `${e.source} → ${e.target}`).toBe(true);
    }
  });

  it('never marks one edge both ways', () => {
    const { upstream, downstream } = connectionsAround(design, 'Chiller/R_CP1');
    expect(upstream.filter((i) => downstream.includes(i))).toEqual([]);
  });

  it('is empty for nodes outside the design', () => {
    expect(connectionsAround(design, 'Dashboard/Nope')).toEqual({ upstream: [], downstream: [] });
    expect(neighbours(design, 'Dashboard/Nope')).toEqual({ upstream: {}, downstream: {} });
  });
});
