import { useLayoutEffect, useMemo, useRef } from 'react';
import { BoxGeometry, Color, InstancedMesh, Matrix4, MeshBasicMaterial } from 'three';
import { connectionsAround } from '../lib/graph';
import { STOREY_M } from '../lib/layout';
import { LAYERS, RISERS, layerOf, routeConnection, type Layer, type Vec3 } from '../lib/routing';
import type { World } from '../lib/world';
import { useConsole } from '../store';
import {
  DIMMED_COLOR,
  DOWNSTREAM_COLOR,
  KIND_COLOR,
  LAYER_COLOR,
  UPSTREAM_COLOR,
} from './palette';

const THICKNESS: Record<Layer, number> = {
  Electrical: 0.22,
  Hydraulic: 0.3,
  Airside: 0.4,
  Network: 0.12,
  Water: 0.2,
};

interface Segment {
  connection: number;
  a: Vec3;
  b: Vec3;
}

const unitBox = new BoxGeometry(1, 1, 1);
const matrix = new Matrix4();
const color = new Color();
const dim = new Color(DIMMED_COLOR);

/** Plant Design connections, routed orthogonally and drawn as one instanced mesh per layer. */
export function Connections({ world }: { world: World }) {
  const explode = useConsole((s) => s.explode);
  const byLayer = useMemo(() => {
    const out = Object.fromEntries(LAYERS.map((l) => [l, [] as Segment[]])) as Record<
      Layer,
      Segment[]
    >;
    world.design.connections.forEach((c, connection) => {
      const route = routeConnection(c, world.layout, explode);
      for (let i = 1; i < route.length; i++) {
        out[layerOf(c.kind)].push({ connection, a: route[i - 1], b: route[i] });
      }
    });
    return out;
  }, [world, explode]);

  return (
    <group>
      {LAYERS.map((layer) => (
        <LayerRuns
          key={`${layer}:${byLayer[layer].length}`}
          world={world}
          layer={layer}
          segments={byLayer[layer]}
        />
      ))}
      <Risers world={world} />
    </group>
  );
}

function LayerRuns({ world, layer, segments }: { world: World; layer: Layer; segments: Segment[] }) {
  const mesh = useRef<InstancedMesh>(null);
  const material = useMemo(() => new MeshBasicMaterial({ toneMapped: false }), []);
  const selected = useConsole((s) => s.selected);
  const visible = useConsole((s) => s.layers[layer]);
  const cutaway = useConsole((s) => s.cutaway);
  const explode = useConsole((s) => s.explode);
  const around = useMemo(
    () => (selected === null ? null : connectionsAround(world.design, selected)),
    [world, selected],
  );

  useLayoutEffect(() => {
    const up = new Set(around?.upstream);
    const down = new Set(around?.downstream);
    const ceiling = cutaway === null ? Infinity : world.layout.elevation(cutaway + 1, explode) - 0.5;
    const t = THICKNESS[layer];
    segments.forEach(({ connection, a: from, b: to }, i) => {
      const lit = up.has(connection) || down.has(connection);
      const hidden = (!visible && !lit) || Math.min(from[1], to[1]) >= ceiling;
      // A riser that climbs past the cut-away ceiling stops there.
      const a: Vec3 = [from[0], Math.min(from[1], ceiling), from[2]];
      const b: Vec3 = [to[0], Math.min(to[1], ceiling), to[2]];
      const w = hidden ? 0 : lit ? t * 1.35 : t;
      const length = Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]) + w;
      const axis = a[0] !== b[0] ? 0 : a[1] !== b[1] ? 1 : 2;
      matrix.makeScale(
        hidden ? 0 : axis === 0 ? length : w,
        hidden ? 0 : axis === 1 ? length : w,
        hidden ? 0 : axis === 2 ? length : w,
      );
      matrix.setPosition((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2);
      mesh.current!.setMatrixAt(i, matrix);

      const kind = world.design.connections[connection].kind;
      if (up.has(connection)) color.set(UPSTREAM_COLOR);
      else if (down.has(connection)) color.set(DOWNSTREAM_COLOR);
      else if (around) color.set(KIND_COLOR[kind]).lerp(dim, 0.7);
      else color.set(KIND_COLOR[kind]);
      mesh.current!.setColorAt(i, color);
    });
    const m = mesh.current!;
    m.instanceMatrix.needsUpdate = true;
    if (m.instanceColor) m.instanceColor.needsUpdate = true;
    m.computeBoundingSphere();
  }, [world, layer, segments, around, visible, cutaway, explode]);

  return (
    <instancedMesh
      ref={mesh}
      args={[unitBox, material, Math.max(segments.length, 1)]}
      raycast={() => null}
    />
  );
}

/** The vertical shafts each layer changes floor in. */
function Risers({ world }: { world: World }) {
  const explode = useConsole((s) => s.explode);
  const layers = useConsole((s) => s.layers);
  const cutaway = useConsole((s) => s.cutaway);
  const top = cutaway ?? world.layout.floors.length - 1;
  const height = world.layout.elevation(top, explode) + STOREY_M;
  return (
    <group>
      {LAYERS.filter((l) => layers[l]).map((layer) => (
        <mesh
          key={layer}
          position={[RISERS[layer].x, height / 2, RISERS[layer].z]}
          raycast={() => null}
        >
          <boxGeometry args={[1.1, height, 1.1]} />
          <meshBasicMaterial
            color={LAYER_COLOR[layer]}
            transparent
            opacity={0.12}
            depthWrite={false}
          />
        </mesh>
      ))}
    </group>
  );
}
