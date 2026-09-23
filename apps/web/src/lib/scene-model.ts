import { familyOf, type Family } from './silhouettes';
import type { PlantDesign } from './types';

/** One instanced draw: every placed asset of one silhouette family, exported or not. */
export interface Batch {
  family: Family;
  unexported: boolean;
  /** Asset path (or `~id`) of each instance, by instance index. */
  paths: string[];
}

export interface SceneModel {
  batches: Batch[];
  instances: Map<string, { batch: number; index: number }>;
  /** The asset behind a raycast hit on `batch`'s instance `index`. */
  pick(batch: number, index: number): string | undefined;
}

export function buildSceneModel(design: PlantDesign): SceneModel {
  const batches: Batch[] = [];
  const byKey = new Map<string, number>();
  const instances = new Map<string, { batch: number; index: number }>();
  for (const a of design.assets) {
    if (a.room === null || a.x === null) continue;
    const family = familyOf(a.typeId);
    const key = `${family}:${a.unexported}`;
    let batch = byKey.get(key);
    if (batch === undefined) {
      batch = batches.push({ family, unexported: a.unexported, paths: [] }) - 1;
      byKey.set(key, batch);
    }
    instances.set(a.path, { batch, index: batches[batch].paths.push(a.path) - 1 });
  }
  return {
    batches,
    instances,
    pick: (batch, index) => batches[batch]?.paths[index],
  };
}

/**
 * The asset a click selects, given every asset under the pointer nearest first: the nearest,
 * or, when the selected asset is under the pointer, the one behind it (wrapping round).
 */
export function nextPick(hits: string[], selected: string | null): string | undefined {
  const at = selected === null ? -1 : hits.indexOf(selected);
  return hits[(at + 1) % hits.length];
}
