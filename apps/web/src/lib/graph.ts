import type { ConnectionKind, PlantDesign } from './types';

type ByKind = Partial<Record<ConnectionKind, string[]>>;

/** Direct suppliers and consumers of a node, by connection kind, in authored order. */
export function neighbours(
  design: PlantDesign,
  node: string,
): { upstream: ByKind; downstream: ByKind } {
  const upstream: ByKind = {};
  const downstream: ByKind = {};
  for (const c of design.connections) {
    if (c.target === node) (upstream[c.kind] ??= []).push(c.source);
    if (c.source === node) (downstream[c.kind] ??= []).push(c.target);
  }
  return { upstream, downstream };
}

/**
 * The connections on every chain into and out of a node, as indices into
 * `design.connections`. Each chain follows one connection kind, as propagation does.
 */
export function connectionsAround(
  design: PlantDesign,
  node: string,
): { upstream: number[]; downstream: number[] } {
  const walk = (forward: boolean): number[] => {
    const found = new Set<number>();
    const kinds = new Set(
      design.connections.filter((c) => (forward ? c.source : c.target) === node).map((c) => c.kind),
    );
    for (const kind of kinds) {
      const seen = new Set([node]);
      const queue = [node];
      while (queue.length) {
        const at = queue.shift()!;
        design.connections.forEach((c, i) => {
          if (c.kind !== kind || (forward ? c.source : c.target) !== at) return;
          found.add(i);
          const next = forward ? c.target : c.source;
          if (!seen.has(next)) {
            seen.add(next);
            queue.push(next);
          }
        });
      }
    }
    return [...found].sort((a, b) => a - b);
  };
  const upstream = walk(false);
  const downstream = walk(true).filter((i) => !upstream.includes(i));
  return { upstream, downstream };
}

/**
 * The causal path of the active faults: the connections downstream of each faulted asset of
 * the kinds its mechanism spreads along (a comm loss follows the control network only, a
 * corrupted reading goes nowhere), as indices into `design.connections`.
 */
export function causalPath(
  design: PlantDesign,
  faults: Iterable<{ target: string; spreadsAlong: readonly ConnectionKind[] }>,
): number[] {
  const found = new Set<number>();
  for (const { target, spreadsAlong } of faults) {
    for (const i of connectionsAround(design, target).downstream) {
      if (spreadsAlong.includes(design.connections[i].kind)) found.add(i);
    }
  }
  return [...found].sort((a, b) => a - b);
}
