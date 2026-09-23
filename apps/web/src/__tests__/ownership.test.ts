import { describe, expect, it } from 'vitest';
import { ownerResolver } from '../lib/ownership';
import { buildTree } from '../lib/tree';
import type { PointInfo } from '../lib/types';
import { buildWorld } from '../lib/world';
import { plantDesign } from './fixtures';

const design = plantDesign();
const assetPaths = design.assets.filter((a) => !a.unexported).map((a) => a.path);
const pointPaths = [
  ...assetPaths.map((p) => `${p}/Status`),
  // A point of a UDT nested inside an Asset belongs to the Asset.
  'Network Switches/MAIN CORE SWITCH A/Ports/Port 01/Link Status',
  ...design.unexported.flatMap((u) => u.observedBy.map((f) => `${f}/Temperature`)),
  'Chiller_System/Chillers/CH-004/Pumps/CHWS-004/Speed',
  'Dashboard/Total IT Load',
  'Other/Gateway 1 Status',
];
const points = pointPaths.map(
  (path): PointInfo => ({ path, sourceClass: 'process_value', alarmBit: false, source: 'physics' }),
);
const ownerOf = ownerResolver(assetPaths, design.unexported);

describe('point ownership', () => {
  it("gives an Asset's points, however deep, to the Asset", () => {
    expect(ownerOf('Chiller/R_C1/Status')).toEqual({ node: 'Chiller/R_C1', at: 'Chiller/R_C1' });
    expect(ownerOf('Network Switches/MAIN CORE SWITCH A/Ports/Port 01/Link Status')).toEqual({
      node: 'Network Switches/MAIN CORE SWITCH A',
      at: 'Network Switches/MAIN CORE SWITCH A',
    });
  });

  it('gives Plant View points to the Unexported Asset they observe', () => {
    expect(ownerOf('Chiller_System/Chillers/CH-004/Pumps/CHWS-004/Speed')).toEqual({
      node: '~CH-004',
      at: 'Chiller_System/Chillers/CH-004',
    });
  });

  it('leaves points outside any Asset or observed folder unowned', () => {
    expect(ownerOf('Other/Gateway 1 Status')).toBeNull();
  });

  it('is the one rule both the inspector and the tree follow', () => {
    const world = buildWorld(design, assetPaths, points);
    const tree = buildTree(assetPaths, pointPaths, design.unexported);
    for (const path of pointPaths) {
      const owner = ownerOf(path);
      if (owner === null) continue;
      expect(world.pointsOf(owner.node).map((p) => p.path), path).toContain(path);
      expect(tree.byPath.get(owner.at)?.selects, path).toBe(owner.node);
      expect(tree.byPath.has(path), path).toBe(false);
    }
  });
});
