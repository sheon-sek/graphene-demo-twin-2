import type { PlantDesign } from './types';

/**
 * World coordinates: plan x → world x, plan y → world z, height → world y, all in metres.
 * Floors stack one storey apart; exploding the stack adds a gap above each floor.
 */
export const STOREY_M = 5;
export const WALL_M = 4.2;
export const EXPLODE_GAP_M = 14;
/** Spacing between assets the Plant Design puts on the same spot. */
const NUDGE_M = 1.2;

export function floorElevation(index: number, explode: number): number {
  return index * (STOREY_M + explode * EXPLODE_GAP_M);
}

export interface RoomBox {
  id: string;
  name: string;
  kind: string;
  floor: string;
  floorIndex: number;
  outdoor: boolean;
  x: number;
  z: number;
  w: number;
  d: number;
}

export interface AssetSpot {
  path: string;
  typeId: string;
  room: string;
  floor: string;
  floorIndex: number;
  x: number;
  z: number;
  unexported: boolean;
}

export interface Layout {
  floors: { name: string; index: number }[];
  rooms: Map<string, RoomBox>;
  /** Placed assets only; Support Assets are not in the building. */
  assets: Map<string, AssetSpot>;
  bounds: { minX: number; maxX: number; minZ: number; maxZ: number };
  elevation(floorIndex: number, explode: number): number;
  /** Floor index of an asset or room node, or null when it is not in the building. */
  floorOf(node: string): number | null;
}

export function buildLayout(design: PlantDesign): Layout {
  const floorIndex = new Map(design.floors.map((f) => [f.name, f.index]));
  const rooms = new Map<string, RoomBox>();
  for (const r of design.rooms) {
    rooms.set(r.id, {
      id: r.id,
      name: r.name,
      kind: r.kind,
      floor: r.floor,
      floorIndex: floorIndex.get(r.floor)!,
      outdoor: r.outdoor,
      x: r.x,
      z: r.y,
      w: r.w,
      d: r.h,
    });
  }

  const assets = new Map<string, AssetSpot>();
  const taken = new Set<string>();
  for (const a of design.assets) {
    if (a.room === null || a.x === null || a.y === null) continue;
    const room = rooms.get(a.room)!;
    let x = a.x;
    const z = a.y;
    // Nudge along x, away from the nearer wall, until the spot is free.
    const step = x + NUDGE_M <= room.x + room.w ? NUDGE_M : -NUDGE_M;
    while (taken.has(`${room.floorIndex}:${x.toFixed(2)}:${z.toFixed(2)}`)) x += step;
    taken.add(`${room.floorIndex}:${x.toFixed(2)}:${z.toFixed(2)}`);
    assets.set(a.path, {
      path: a.path,
      typeId: a.typeId,
      room: room.id,
      floor: room.floor,
      floorIndex: room.floorIndex,
      x,
      z,
      unexported: a.unexported,
    });
  }

  const boxes = [...rooms.values()];
  const bounds = {
    minX: Math.min(...boxes.map((r) => r.x)),
    maxX: Math.max(...boxes.map((r) => r.x + r.w)),
    minZ: Math.min(...boxes.map((r) => r.z)),
    maxZ: Math.max(...boxes.map((r) => r.z + r.d)),
  };

  return {
    floors: [...design.floors].sort((a, b) => a.index - b.index),
    rooms,
    assets,
    bounds,
    elevation: floorElevation,
    floorOf(node) {
      return assets.get(node)?.floorIndex ?? rooms.get(node)?.floorIndex ?? null;
    },
  };
}
