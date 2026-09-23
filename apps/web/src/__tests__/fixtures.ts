import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { PlantDesign } from '../lib/types';

/** The committed Plant Design in the shape `/api/plant-design` serves it. */
export function plantDesign(): PlantDesign {
  const raw = JSON.parse(
    readFileSync(resolve(__dirname, '../../../../plant-design/plant-design.json'), 'utf8'),
  );
  return {
    version: raw.version,
    floors: raw.floors.map((name: string, index: number) => ({ name, index })),
    rooms: raw.rooms.map((r: Record<string, unknown>) => ({
      id: r.id,
      floor: r.floor,
      name: r.name,
      x: r.x,
      y: r.y,
      w: r.w,
      h: r.h,
      kind: r.kind,
      fireZone: r.fire,
      outdoor: r.outdoor,
    })),
    assets: raw.assets.map((a: Record<string, unknown>) => ({
      path: a.path,
      typeId: a.type,
      room: a.room,
      x: a.x,
      y: a.y,
      role: a.role,
      system: a.sys,
      unexported: a.unexported,
      support: a.room === null,
    })),
    unexported: raw.unexported.map((u: Record<string, unknown>) => ({
      id: u.id,
      name: u.name,
      typeId: u.type,
      observedBy: u.observedBy,
    })),
    connections: raw.edges.map((e: Record<string, unknown>) => ({
      kind: e.kind,
      source: e.a,
      target: e.b,
      label: e.label ?? '',
    })),
    shafts: raw.shafts,
  };
}

/** A Plant Design node is placed when it has a room and a plan position. */
export function placedPaths(design: PlantDesign): string[] {
  return design.assets.filter((a) => a.room !== null && a.x !== null).map((a) => a.path);
}
