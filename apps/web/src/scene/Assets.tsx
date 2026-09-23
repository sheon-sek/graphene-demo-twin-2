import { useFrame, type ThreeEvent } from '@react-three/fiber';
import { useEffect, useLayoutEffect, useMemo, useRef } from 'react';
import {
  BackSide,
  Color,
  InstancedBufferAttribute,
  InstancedMesh,
  Matrix4,
  Mesh,
  MeshBasicMaterial,
  MeshStandardMaterial,
} from 'three';
import { locate } from '../actions';
import type { LiveStore } from '../lib/live';
import { nextPick, type Batch } from '../lib/scene-model';
import { familyGeometry, familyOf } from '../lib/silhouettes';
import { assetStatus } from '../lib/status';
import type { World } from '../lib/world';
import { useConsole } from '../store';
import { SELECTED_COLOR, STATUS_COLOR } from './palette';

/** Every placed asset: one instanced draw per silhouette family (and per exported-ness). */
export function Assets({ world, live }: { world: World; live: LiveStore }) {
  return (
    <group>
      {world.scene.batches.map((batch, i) => (
        <AssetBatch key={i} world={world} live={live} batch={batch} />
      ))}
      <SelectionMarker world={world} />
    </group>
  );
}

/** Bad quality is drawn with screen-space hatching, driven by a per-instance attribute. */
function assetMaterial(unexported: boolean): MeshStandardMaterial {
  const material = new MeshStandardMaterial({
    flatShading: true,
    roughness: 0.75,
    metalness: 0.05,
    transparent: unexported,
    opacity: unexported ? 0.35 : 1,
    depthWrite: !unexported,
  });
  material.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\nattribute float hatch;\nvarying float vHatch;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvHatch = hatch;');
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', '#include <common>\nvarying float vHatch;')
      .replace(
        '#include <dithering_fragment>',
        '#include <dithering_fragment>\nif (vHatch > 0.5 && mod(gl_FragCoord.x + gl_FragCoord.y, 9.0) < 4.0) gl_FragColor.rgb *= 0.3;',
      );
  };
  return material;
}

/** Every asset under the pointer, nearest first. */
function hitsOf(e: ThreeEvent<MouseEvent>): string[] {
  const hits = e.intersections.flatMap(({ object, instanceId }) => {
    const paths = object.userData.paths as string[] | undefined;
    return paths && instanceId !== undefined ? [paths[instanceId]] : [];
  });
  return [...new Set(hits)];
}

const matrix = new Matrix4();
const color = new Color();

function AssetBatch({ world, live, batch }: { world: World; live: LiveStore; batch: Batch }) {
  const mesh = useRef<InstancedMesh>(null);
  const ghost = useRef<InstancedMesh>(null);
  const explode = useConsole((s) => s.explode);
  const cutaway = useConsole((s) => s.cutaway);
  const count = batch.paths.length;

  const geometry = useMemo(() => {
    const g = familyGeometry(batch.family).clone();
    g.setAttribute('hatch', new InstancedBufferAttribute(new Float32Array(count), 1));
    return g;
  }, [batch, count]);
  const material = useMemo(() => assetMaterial(batch.unexported), [batch]);
  const wire = useMemo(
    () =>
      new MeshBasicMaterial({ color: '#dbe7f3', wireframe: true, transparent: true, opacity: 0.7 }),
    [],
  );
  useEffect(() => () => geometry.dispose(), [geometry]);

  useLayoutEffect(() => {
    const { layout } = world;
    batch.paths.forEach((path, i) => {
      const spot = layout.assets.get(path)!;
      const hidden = cutaway !== null && spot.floorIndex > cutaway;
      matrix.makeScale(hidden ? 0 : 1, hidden ? 0 : 1, hidden ? 0 : 1);
      matrix.setPosition(spot.x, layout.elevation(spot.floorIndex, explode), spot.z);
      mesh.current!.setMatrixAt(i, matrix);
      ghost.current?.setMatrixAt(i, matrix);
    });
    for (const m of [mesh.current, ghost.current]) {
      if (!m) continue;
      m.instanceMatrix.needsUpdate = true;
      m.computeBoundingSphere();
      m.computeBoundingBox();
    }
  }, [world, batch, explode, cutaway]);

  useLayoutEffect(() => {
    const hatch = geometry.getAttribute('hatch') as InstancedBufferAttribute;
    const paint = () => {
      batch.paths.forEach((path, i) => {
        const status = assetStatus(
          world.pointsOf(path).map((p) => ({ ...p, reading: live.reading(p.path) })),
        );
        mesh.current!.setColorAt(i, color.set(STATUS_COLOR[status]));
        hatch.setX(i, status === 'bad' ? 1 : 0);
      });
      mesh.current!.instanceColor!.needsUpdate = true;
      hatch.needsUpdate = true;
    };
    paint();
    return live.subscribe(paint);
  }, [world, live, batch, geometry]);

  const pathOf = (e: ThreeEvent<PointerEvent>) =>
    e.instanceId === undefined ? undefined : batch.paths[e.instanceId];

  return (
    <>
      <instancedMesh
        ref={mesh}
        args={[geometry, material, count]}
        userData={{ paths: batch.paths }}
        onClick={(e) => {
          e.stopPropagation();
          // A second click on the selected asset reaches the one behind it.
          const path = nextPick(hitsOf(e), useConsole.getState().selected);
          if (path) useConsole.getState().select(path);
        }}
        onDoubleClick={(e) => {
          e.stopPropagation();
          const hits = hitsOf(e);
          const selected = useConsole.getState().selected;
          const path = selected !== null && hits.includes(selected) ? selected : hits[0];
          if (path) locate(world, path);
        }}
        onPointerMove={(e) => {
          e.stopPropagation();
          const path = pathOf(e) ?? null;
          if (useConsole.getState().hovered !== path) useConsole.getState().hover(path);
          document.body.style.cursor = 'pointer';
        }}
        onPointerOut={() => {
          useConsole.getState().hover(null);
          document.body.style.cursor = '';
        }}
      />
      {batch.unexported && (
        <instancedMesh ref={ghost} args={[geometry, wire, count]} raycast={() => null} />
      )}
    </>
  );
}

/** A bright shell and a pulsing ring around the selected asset. */
function SelectionMarker({ world }: { world: World }) {
  const selected = useConsole((s) => s.selected);
  const explode = useConsole((s) => s.explode);
  const ring = useRef<Mesh>(null);
  const spot = selected === null ? undefined : world.layout.assets.get(selected);
  const geometry = spot ? familyGeometry(familyOf(spot.typeId)) : undefined;
  const radius = Math.max(0.8, (geometry?.boundingSphere?.radius ?? 1) * 1.1);

  useFrame(({ clock }) => {
    if (!ring.current) return;
    const s = 1 + 0.12 * Math.sin(clock.elapsedTime * 4);
    ring.current.scale.set(s, s, 1);
  });

  if (!spot || !geometry) return null;
  const y = world.layout.elevation(spot.floorIndex, explode);
  return (
    <group position={[spot.x, y, spot.z]}>
      <mesh geometry={geometry} scale={1.1} raycast={() => null}>
        <meshBasicMaterial color={SELECTED_COLOR} side={BackSide} />
      </mesh>
      <mesh ref={ring} rotation-x={-Math.PI / 2} position-y={0.12} raycast={() => null}>
        <ringGeometry args={[radius, radius + 0.25, 32]} />
        <meshBasicMaterial color={SELECTED_COLOR} transparent opacity={0.85} />
      </mesh>
    </group>
  );
}
