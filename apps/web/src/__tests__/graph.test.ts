import { describe, expect, it } from 'vitest';
import { causalPath, connectionsAround, neighbours } from '../lib/graph';
import type { ConnectionKind } from '../lib/types';
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

describe('the causal path of injected faults', () => {
  const CRAC3 = 'CRAC/L1_CRAC3';
  const SWITCH = 'Network Topology/SERVER DISTRIBUTION SWITCH A';
  const physical: ConnectionKind[] = ['power', 'chw', 'cw', 'air', 'water', 'fuel'];
  const pairs = (path: number[]) => path.map((i) => [edge(i).source, edge(i).target]);

  it('follows Plant Design connections downstream of each faulted asset', () => {
    const trip = causalPath(design, [{ target: CRAC3, spreadsAlong: physical }]);
    expect(pairs(trip)).toEqual([[CRAC3, 'DH03']]);
    const both = causalPath(design, [
      { target: CRAC3, spreadsAlong: physical },
      { target: SWITCH, spreadsAlong: ['net'] },
    ]);
    expect(both).toContain(trip[0]);
    expect(both.map((i) => edge(i).kind).filter((k) => k === 'net').length).toBe(7);
    expect(causalPath(design, [])).toEqual([]);
  });

  it("follows only the connection kinds the fault's mechanism spreads along", () => {
    // A comm loss changes the CRAC's point quality only: the hall it cools is unchanged.
    expect(causalPath(design, [{ target: CRAC3, spreadsAlong: ['net'] }])).toEqual([]);
    // A corrupted sensor reading goes nowhere.
    expect(causalPath(design, [{ target: CRAC3, spreadsAlong: [] }])).toEqual([]);
    // A physical fault never lights the control network.
    expect(causalPath(design, [{ target: SWITCH, spreadsAlong: physical }])).toEqual([]);
  });
});
