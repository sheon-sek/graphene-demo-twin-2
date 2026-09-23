import type { UnexportedAsset } from './types';

/** Who a point belongs to: `node` is the Plant Design node, `at` the export path it hangs off. */
export interface Owner {
  /** The Asset's export path, or the `~id` of the Unexported Asset a Plant View folder observes. */
  node: string;
  /** The Asset itself, or the observing Plant View folder. */
  at: string;
}

export type OwnerOf = (pointPath: string) => Owner | null;

/**
 * The one ownership rule the inspector and the tree share: a point belongs to its nearest
 * ancestor that is an Asset or a Plant View folder observing an Unexported Asset.
 */
export function ownerResolver(
  assetPaths: Iterable<string>,
  unexported: UnexportedAsset[],
): OwnerOf {
  const assets = new Set(assetPaths);
  const observers = new Map<string, string>();
  for (const u of unexported) for (const folder of u.observedBy) observers.set(folder, u.id);
  return (path) => {
    for (let cut = path.lastIndexOf('/'); cut > 0; cut = path.lastIndexOf('/', cut - 1)) {
      const at = path.slice(0, cut);
      const node = assets.has(at) ? at : observers.get(at);
      if (node !== undefined) return { node, at };
    }
    return null;
  };
}
