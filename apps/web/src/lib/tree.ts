import { ownerResolver } from './ownership';
import type { UnexportedAsset } from './types';

/**
 * The Asset Model tree: folders and Assets in exact export hierarchy and order. Assets are
 * leaves; their points are shown by the inspector. A Plant View folder that observes an
 * Unexported Asset selects that asset.
 */
export interface TreeNode {
  path: string;
  name: string;
  parent: string | null;
  kind: 'folder' | 'asset';
  children: string[];
  /** The Plant Design node this row selects, if any. */
  selects: string | null;
}

export interface Tree {
  roots: string[];
  byPath: Map<string, TreeNode>;
  /** Plant Design node (asset path or `~id`) → the tree node that selects it. */
  nodeFor: Map<string, string>;
}

export interface TreeRow {
  path: string;
  depth: number;
}

export function buildTree(
  assetPaths: Iterable<string>,
  pointPaths: Iterable<string>,
  unexported: UnexportedAsset[],
): Tree {
  const assets = new Set(assetPaths);
  const tree: Tree = { roots: [], byPath: new Map(), nodeFor: new Map() };

  const add = (path: string) => {
    if (tree.byPath.has(path)) return;
    const cut = path.lastIndexOf('/');
    const parent = cut < 0 ? null : path.slice(0, cut);
    if (parent !== null) add(parent);
    const isAsset = assets.has(path);
    tree.byPath.set(path, {
      path,
      name: path.slice(cut + 1),
      parent,
      kind: isAsset ? 'asset' : 'folder',
      children: [],
      selects: isAsset ? path : null,
    });
    if (parent === null) tree.roots.push(path);
    else tree.byPath.get(parent)!.children.push(path);
    if (isAsset) tree.nodeFor.set(path, path);
  };

  // An Asset's points, however deep, stay inside it; other points show their own folder.
  const ownerOf = ownerResolver(assets, unexported);
  for (const point of pointPaths) {
    const owner = ownerOf(point);
    if (owner !== null && assets.has(owner.node)) add(owner.at);
    else {
      const cut = point.lastIndexOf('/');
      if (cut > 0) add(point.slice(0, cut));
    }
  }
  for (const asset of assets) add(asset);

  // A folder selects the first Unexported Asset it observes (a fire zone, listed before its
  // devices); each later one sharing the folder gets a leaf of its own inside it.
  for (const u of unexported) {
    for (const folder of u.observedBy) {
      add(folder);
      const node = tree.byPath.get(folder)!;
      if (node.selects === null || node.selects === u.id) {
        node.selects = u.id;
        if (!tree.nodeFor.has(u.id)) tree.nodeFor.set(u.id, folder);
      } else if (!tree.nodeFor.has(u.id)) {
        const leaf = `${folder}/${u.id}`;
        tree.byPath.set(leaf, {
          path: leaf,
          name: u.name,
          parent: folder,
          kind: 'asset',
          children: [],
          selects: u.id,
        });
        node.children.push(leaf);
        tree.nodeFor.set(u.id, leaf);
      }
    }
  }
  return tree;
}

/** The folders to expand, outermost first, so that `path` is visible. */
export function ancestorsOf(tree: Tree, path: string): string[] {
  const out: string[] = [];
  for (let p = tree.byPath.get(path)?.parent ?? null; p !== null; ) {
    out.unshift(p);
    p = tree.byPath.get(p)?.parent ?? null;
  }
  return out;
}

export function flattenVisible(tree: Tree, expanded: ReadonlySet<string>): TreeRow[] {
  const rows: TreeRow[] = [];
  const visit = (path: string, depth: number) => {
    rows.push({ path, depth });
    if (expanded.has(path)) {
      for (const child of tree.byPath.get(path)!.children) visit(child, depth + 1);
    }
  };
  for (const root of tree.roots) visit(root, 0);
  return rows;
}
