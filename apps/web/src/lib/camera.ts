import type { Layout } from './layout';
import { familyFocus, familyOf } from './silhouettes';

/** What the camera frames: site → floor → hall or plant room → asset. */
export type View =
  | { kind: 'site' }
  | { kind: 'floor'; floor: string }
  | { kind: 'room'; room: string }
  | { kind: 'asset'; path: string };

export interface CameraPose {
  position: [number, number, number];
  target: [number, number, number];
}

/** Camera heading: from the south-east, looking north-west and down. */
const HEADING: [number, number, number] = normalise([0.55, 0.62, 0.56]);

function normalise(v: [number, number, number]): [number, number, number] {
  const n = Math.hypot(...v);
  return [v[0] / n, v[1] / n, v[2] / n];
}

function pose(target: [number, number, number], distance: number): CameraPose {
  return {
    target,
    position: [
      target[0] + HEADING[0] * distance,
      target[1] + HEADING[1] * distance,
      target[2] + HEADING[2] * distance,
    ],
  };
}

export function cameraFor(view: View, layout: Layout, explode: number): CameraPose {
  const { minX, maxX, minZ, maxZ } = layout.bounds;
  const cx = (minX + maxX) / 2;
  const cz = (minZ + maxZ) / 2;
  const span = Math.max(maxX - minX, maxZ - minZ);
  switch (view.kind) {
    case 'site': {
      const top = layout.elevation(layout.floors.length - 1, explode);
      return pose([cx, top / 2, cz], span * 1.25 + top);
    }
    case 'floor': {
      const floor = layout.floors.find((f) => f.name === view.floor);
      if (!floor) return cameraFor({ kind: 'site' }, layout, explode);
      const rooms = [...layout.rooms.values()].filter((r) => r.floorIndex === floor.index);
      const x0 = Math.min(...rooms.map((r) => r.x));
      const x1 = Math.max(...rooms.map((r) => r.x + r.w));
      const z0 = Math.min(...rooms.map((r) => r.z));
      const z1 = Math.max(...rooms.map((r) => r.z + r.d));
      const y = layout.elevation(floor.index, explode);
      return pose([(x0 + x1) / 2, y, (z0 + z1) / 2], Math.max(x1 - x0, z1 - z0) * 1.15);
    }
    case 'room': {
      const room = layout.rooms.get(view.room);
      if (!room) return cameraFor({ kind: 'site' }, layout, explode);
      const y = layout.elevation(room.floorIndex, explode);
      return pose([room.x + room.w / 2, y, room.z + room.d / 2], Math.max(room.w, room.d) * 1.3 + 8);
    }
    case 'asset': {
      const asset = layout.assets.get(view.path);
      if (!asset) return cameraFor({ kind: 'site' }, layout, explode);
      const y = layout.elevation(asset.floorIndex, explode) + familyFocus(familyOf(asset.typeId));
      return pose([asset.x, y, asset.z], 11);
    }
  }
}

/** The floor and room a node sits in, for the site → floor → room → asset trail. */
export function contextOf(
  node: string,
  layout: Layout,
): { floor: string | null; room: string | null } {
  const asset = layout.assets.get(node);
  if (!asset) return { floor: null, room: null };
  return { floor: asset.floor, room: asset.room };
}
