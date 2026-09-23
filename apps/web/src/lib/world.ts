import { buildLayout, type Layout } from './layout';
import { ownerResolver } from './ownership';
import { buildSceneModel, type SceneModel } from './scene-model';
import { buildSearchIndex, type SearchIndex } from './search';
import { buildTree, type Tree } from './tree';
import type { PlacedAsset, PlantDesign, PointInfo } from './types';

/** Everything static the console derives once from the Plant Design and the Asset Model. */
export interface World {
  design: PlantDesign;
  layout: Layout;
  scene: SceneModel;
  tree: Tree;
  search: SearchIndex;
  /** Plant Design node (asset path or `~id`) → its placement, if it is in the design. */
  nodes: Map<string, PlacedAsset>;
  /** The points that observe a node: an Asset's own, or an Unexported Asset's Plant View points. */
  pointsOf(node: string): PointInfo[];
}

export function buildWorld(
  design: PlantDesign,
  assetPaths: string[],
  points: PointInfo[],
): World {
  const ownerOf = ownerResolver(assetPaths, design.unexported);
  const owned = new Map<string, PointInfo[]>();
  for (const point of points) {
    const owner = ownerOf(point.path);
    if (owner === null) continue;
    let list = owned.get(owner.node);
    if (!list) owned.set(owner.node, (list = []));
    list.push(point);
  }

  return {
    design,
    layout: buildLayout(design),
    scene: buildSceneModel(design),
    tree: buildTree(
      assetPaths,
      points.map((p) => p.path),
      design.unexported,
    ),
    search: buildSearchIndex(assetPaths, design.unexported),
    nodes: new Map(design.assets.map((a) => [a.path, a])),
    pointsOf: (node) => owned.get(node) ?? [],
  };
}
