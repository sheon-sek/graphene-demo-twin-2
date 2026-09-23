import type { Layout } from './layout';
import type { Connection, ConnectionKind } from './types';

export type Vec3 = [number, number, number];

export const LAYERS = ['Electrical', 'Hydraulic', 'Airside', 'Network', 'Water'] as const;
export type Layer = (typeof LAYERS)[number];

const LAYER_OF: Record<ConnectionKind, Layer> = {
  power: 'Electrical',
  // Diesel feeds the gensets: it belongs to the standby power chain.
  fuel: 'Electrical',
  chw: 'Hydraulic',
  cw: 'Hydraulic',
  air: 'Airside',
  net: 'Network',
  water: 'Water',
};

export function layerOf(kind: ConnectionKind): Layer {
  return LAYER_OF[kind];
}

/** Height of each layer's ceiling tray above its floor, so layers never share a run. */
const TRAY_M: Record<Layer, number> = {
  Electrical: 4.0,
  Network: 3.75,
  Hydraulic: 3.5,
  Water: 3.25,
  Airside: 3.0,
};

/**
 * Where each layer changes floor: one vertical shaft per layer, side by side in the corridor
 * next to the lift core. The Plant Design places no risers, so this is a drawing convention.
 */
export const RISERS: Record<Layer, { x: number; z: number }> = {
  Electrical: { x: 45, z: 34.5 },
  Hydraulic: { x: 47, z: 34.5 },
  Airside: { x: 49, z: 34.5 },
  Water: { x: 51, z: 34.5 },
  Network: { x: 53, z: 34.5 },
};

/** Height above the floor at which a connection leaves or enters an asset or room. */
const ANCHOR_M = 1.0;

/** Where a node's connections attach: the asset itself, or a room's centre. */
export function anchorOf(node: string, layout: Layout, explode: number): Vec3 {
  const asset = layout.assets.get(node);
  if (asset) return [asset.x, layout.elevation(asset.floorIndex, explode) + ANCHOR_M, asset.z];
  const room = layout.rooms.get(node);
  if (room) {
    return [
      room.x + room.w / 2,
      layout.elevation(room.floorIndex, explode) + ANCHOR_M,
      room.z + room.d / 2,
    ];
  }
  throw new Error(`not in the building: ${node}`);
}

/**
 * An orthogonal polyline from source to target: up to the layer's tray, along x then z, and
 * down again. Between floors the run goes via the layer's riser.
 */
export function routeConnection(c: Connection, layout: Layout, explode: number): Vec3[] {
  const layer = layerOf(c.kind);
  const from = anchorOf(c.source, layout, explode);
  const to = anchorOf(c.target, layout, explode);
  const fromFloor = layout.floorOf(c.source)!;
  const toFloor = layout.floorOf(c.target)!;
  const trayFrom = layout.elevation(fromFloor, explode) + TRAY_M[layer];
  const trayTo = layout.elevation(toFloor, explode) + TRAY_M[layer];

  const points: Vec3[] = [from, [from[0], trayFrom, from[2]]];
  if (fromFloor === toFloor) {
    points.push([to[0], trayFrom, from[2]], [to[0], trayFrom, to[2]]);
  } else {
    const riser = RISERS[layer];
    points.push(
      [riser.x, trayFrom, from[2]],
      [riser.x, trayFrom, riser.z],
      [riser.x, trayTo, riser.z],
      [to[0], trayTo, riser.z],
      [to[0], trayTo, to[2]],
    );
  }
  points.push(to);
  return simplify(points);
}

/** Drop repeated points and merge collinear runs, so every step changes exactly one axis. */
function simplify(points: Vec3[]): Vec3[] {
  const distinct = points.filter(
    (p, i) => i === 0 || p.some((v, axis) => v !== points[i - 1][axis]),
  );
  const out: Vec3[] = [];
  for (const p of distinct) {
    if (out.length >= 2) {
      const [a, b] = [out[out.length - 2], out[out.length - 1]];
      if (axisOf(a, b) === axisOf(b, p)) out.pop();
    }
    out.push(p);
  }
  if (out.length === 1) out.push([...out[0]]);
  return out;
}

function axisOf(a: Vec3, b: Vec3): number {
  return a[0] !== b[0] ? 0 : a[1] !== b[1] ? 1 : 2;
}
