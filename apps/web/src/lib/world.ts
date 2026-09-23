import { buildLayout, type Layout } from './layout';
import { buildSceneModel, type SceneModel } from './scene-model';
import { buildSearchIndex, type SearchIndex } from './search';
import { buildTree, type Tree } from './tree';
import type { FaultSpec, PlacedAsset, PlantDesign, PointInfo } from './types';

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
  /** The node a point observes, if any. */
  ownerOf(path: string): string | undefined;
  /** Every fault/alarm point, in export order. */
  alarmPoints: PointInfo[];
  /** The fault catalog. */
  catalog: FaultSpec[];
}

export function buildWorld(
  design: PlantDesign,
  assetPaths: string[],
  points: PointInfo[],
  catalog: FaultSpec[] = [],
): World {
  const assets = new Set(assetPaths);
  const owned = new Map<string, PointInfo[]>();
  const observers = new Map<string, string>();
  const owners = new Map<string, string>();
  for (const u of design.unexported) for (const folder of u.observedBy) observers.set(folder, u.id);

  for (const point of points) {
    for (let cut = point.path.lastIndexOf('/'); cut > 0; cut = point.path.lastIndexOf('/', cut - 1)) {
      const prefix = point.path.slice(0, cut);
      const owner = assets.has(prefix) ? prefix : observers.get(prefix);
      if (owner !== undefined) {
        let list = owned.get(owner);
        if (!list) owned.set(owner, (list = []));
        list.push(point);
        owners.set(point.path, owner);
        break;
      }
    }
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
    ownerOf: (path) => owners.get(path),
    alarmPoints: points.filter((p) => p.sourceClass === 'fault_alarm'),
    catalog,
  };
}
