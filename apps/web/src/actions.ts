import type { World } from './lib/world';
import { useConsole } from './store';

/** Select a node and fly to it, cutting away the floors above it. */
export function locate(world: World, node: string): void {
  if (!world.nodes.has(node) && !world.tree.nodeFor.has(node)) return;
  const { select, goTo } = useConsole.getState();
  select(node);
  const spot = world.layout.assets.get(node);
  if (spot) goTo({ kind: 'asset', path: node }, spot.floorIndex);
}

export function showSite(): void {
  useConsole.getState().goTo({ kind: 'site' }, null);
}

export function showFloor(world: World, floor: string): void {
  const index = world.layout.floors.find((f) => f.name === floor)?.index ?? null;
  useConsole.getState().goTo({ kind: 'floor', floor }, index);
}

export function showRoom(world: World, room: string): void {
  const box = world.layout.rooms.get(room);
  useConsole.getState().goTo({ kind: 'room', room }, box?.floorIndex ?? null);
}
