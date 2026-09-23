import { describe, expect, it } from 'vitest';
import { ancestorsOf, buildTree, flattenVisible, type TreeNode } from '../lib/tree';
import { placedPaths, plantDesign } from './fixtures';

const design = plantDesign();
const exported = design.assets.filter((a) => !a.unexported).map((a) => a.path);
// Every asset owns at least one point; Plant View folders hold points of their own.
const pointPaths = [
  ...exported.map((p) => `${p}/Status`),
  ...design.unexported.flatMap((u) => u.observedBy.map((f) => `${f}/Temperature`)),
  'Dashboard/Total IT Load',
];
const tree = buildTree(exported, pointPaths, design.unexported);

function find(path: string): TreeNode | undefined {
  return tree.byPath.get(path);
}

describe('Asset Model tree', () => {
  it('mirrors the export hierarchy, one node per folder and asset', () => {
    const chiller = find('Chiller/R_C1');
    expect(chiller?.kind).toBe('asset');
    expect(chiller?.name).toBe('R_C1');
    expect(chiller?.parent).toBe('Chiller');
    expect(find('Chiller')?.kind).toBe('folder');
    expect(find('Chiller')?.children).toContain('Chiller/R_C1');
    expect(tree.roots).toContain('Chiller');
  });

  it('stops at assets: their points belong to the inspector, not the tree', () => {
    expect(find('Chiller/R_C1')?.children).toEqual([]);
    expect(find('Chiller/R_C1/Status')).toBeUndefined();
  });

  it('keeps export order rather than sorting', () => {
    expect(tree.roots.indexOf('AC Makeup Tank')).toBeLessThan(tree.roots.indexOf('Chiller'));
  });

  it('shows Plant View folders, and their observer folders select the Unexported Asset', () => {
    const view = find('Chiller System Control/Chillers/CH-004');
    expect(view?.kind).toBe('folder');
    expect(view?.selects).toBe('~CH-004');
    expect(find('Chiller_System/Chillers/CH-004')?.selects).toBe('~CH-004');
    expect(tree.nodeFor.get('~CH-004')).toBe('Chiller System Control/Chillers/CH-004');
  });

  it('gives every placed asset a tree node that selects it', () => {
    for (const path of placedPaths(design)) {
      const key = tree.nodeFor.get(path);
      expect(key, path).toBeDefined();
      expect(tree.byPath.get(key!)?.selects, path).toBe(path);
    }
  });

  it('lists the ancestors to expand to reveal a node', () => {
    expect(ancestorsOf(tree, 'Chiller System Control/Chillers/CH-004')).toEqual([
      'Chiller System Control',
      'Chiller System Control/Chillers',
    ]);
    expect(ancestorsOf(tree, 'Chiller')).toEqual([]);
  });

  it('flattens only the expanded branches, with depth', () => {
    const rows = flattenVisible(tree, new Set(['Chiller']));
    const chiller = rows.findIndex((r) => r.path === 'Chiller');
    expect(rows[chiller + 1]).toMatchObject({ path: 'Chiller/R_C1', depth: 1 });
    expect(rows.some((r) => r.path === 'Chiller System Control/Chillers')).toBe(false);
    expect(rows.filter((r) => r.depth === 0).map((r) => r.path)).toEqual(tree.roots);
  });
});
