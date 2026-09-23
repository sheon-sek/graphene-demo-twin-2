import { Html } from '@react-three/drei';
import { useLayoutEffect, useMemo, useRef } from 'react';
import { BoxGeometry, Color, InstancedMesh, Matrix4, MeshBasicMaterial, MeshStandardMaterial } from 'three';
import { showRoom } from '../actions';
import { WALL_M, type RoomBox } from '../lib/layout';
import type { World } from '../lib/world';
import { useConsole } from '../store';
import { ROOM_COLOR } from './palette';

const unitBox = new BoxGeometry(1, 1, 1);
const matrix = new Matrix4();
const color = new Color();
const SLAB_M = 0.3;
const WALL_T = 0.15;
const INSET = 0.25;

interface Wall {
  room: RoomBox;
  x: number;
  z: number;
  w: number;
  d: number;
}

function wallsOf(room: RoomBox): Wall[] {
  const { x, z, w, d } = room;
  return [
    { room, x: x + w / 2, z, w, d: WALL_T },
    { room, x: x + w / 2, z: z + d, w, d: WALL_T },
    { room, x, z: z + d / 2, w: WALL_T, d },
    { room, x: x + w, z: z + d / 2, w: WALL_T, d },
  ];
}

/** Floor slabs, room tiles and semi-transparent room walls, following the exploded stack. */
export function Building({ world }: { world: World }) {
  const { layout } = world;
  const explode = useConsole((s) => s.explode);
  const cutaway = useConsole((s) => s.cutaway);
  const rooms = useMemo(() => [...layout.rooms.values()], [layout]);
  const walls = useMemo(() => rooms.filter((r) => !r.outdoor).flatMap(wallsOf), [rooms]);
  const tiles = useRef<InstancedMesh>(null);
  const wallMesh = useRef<InstancedMesh>(null);
  const rails = useRef<InstancedMesh>(null);
  const materials = useMemo(
    () => ({
      tile: new MeshStandardMaterial({ roughness: 1 }),
      wall: new MeshBasicMaterial({
        color: '#9fb4c8',
        transparent: true,
        opacity: 0.1,
        depthWrite: false,
      }),
      rail: new MeshBasicMaterial({ color: '#5b6b7d' }),
      slab: new MeshStandardMaterial({ color: '#10161d', roughness: 1 }),
    }),
    [],
  );
  const shown = (floorIndex: number) => cutaway === null || floorIndex <= cutaway;

  useLayoutEffect(() => {
    rooms.forEach((r, i) => {
      const y = layout.elevation(r.floorIndex, explode);
      const s = shown(r.floorIndex) ? 1 : 0;
      matrix.makeScale((r.w - INSET * 2) * s, 0.06 * s, (r.d - INSET * 2) * s);
      matrix.setPosition(r.x + r.w / 2, y + 0.03, r.z + r.d / 2);
      tiles.current!.setMatrixAt(i, matrix);
      tiles.current!.setColorAt(i, color.set(ROOM_COLOR[r.kind] ?? ROOM_COLOR.support));
    });
    walls.forEach((wall, i) => {
      const y = layout.elevation(wall.room.floorIndex, explode);
      const s = shown(wall.room.floorIndex) ? 1 : 0;
      matrix.makeScale(wall.w * s, WALL_M * s, wall.d * s);
      matrix.setPosition(wall.x, y + WALL_M / 2, wall.z);
      wallMesh.current!.setMatrixAt(i, matrix);
      matrix.makeScale(wall.w * s, 0.08 * s, wall.d * s);
      matrix.setPosition(wall.x, y + WALL_M, wall.z);
      rails.current!.setMatrixAt(i, matrix);
    });
    for (const m of [tiles.current!, wallMesh.current!, rails.current!]) {
      m.instanceMatrix.needsUpdate = true;
      if (m.instanceColor) m.instanceColor.needsUpdate = true;
      m.computeBoundingSphere();
    }
  }, [layout, rooms, walls, explode, cutaway]);

  const slabs = useMemo(
    () =>
      layout.floors.map((f) => {
        const own = rooms.filter((r) => r.floorIndex === f.index);
        const minX = Math.min(...own.map((r) => r.x));
        const maxX = Math.max(...own.map((r) => r.x + r.w));
        const minZ = Math.min(...own.map((r) => r.z));
        const maxZ = Math.max(...own.map((r) => r.z + r.d));
        return { ...f, minX, maxX, minZ, maxZ };
      }),
    [layout, rooms],
  );

  return (
    <group>
      {slabs.map((s) => (
        shown(s.index) && (
        <group key={s.name}>
          <mesh
            material={materials.slab}
            position={[
              (s.minX + s.maxX) / 2,
              layout.elevation(s.index, explode) - SLAB_M / 2,
              (s.minZ + s.maxZ) / 2,
            ]}
            scale={[s.maxX - s.minX + 1, SLAB_M, s.maxZ - s.minZ + 1]}
            geometry={unitBox}
            raycast={() => null}
          />
          <Html
            position={[s.minX - 1, layout.elevation(s.index, explode) + 0.5, s.maxZ + 1]}
            className="floor-label"
            center
          >
            {s.name}
          </Html>
        </group>
        )
      ))}
      <instancedMesh
        ref={tiles}
        args={[unitBox, materials.tile, rooms.length]}
        onDoubleClick={(e) => {
          if (e.instanceId === undefined) return;
          // An asset standing on the tile ties with it for nearest hit: the asset takes it.
          const onTile = e.intersections.some(
            (i) => i.object.userData.paths && i.distance <= e.distance + 1e-3,
          );
          if (onTile) return;
          e.stopPropagation();
          showRoom(world, rooms[e.instanceId].id);
        }}
      />
      <instancedMesh ref={wallMesh} args={[unitBox, materials.wall, walls.length]} raycast={() => null} />
      <instancedMesh ref={rails} args={[unitBox, materials.rail, walls.length]} raycast={() => null} />
      {cutaway !== null &&
        rooms
          .filter((r) => r.floorIndex === cutaway)
          .map((r) => (
            <Html
              key={r.id}
              position={[r.x + r.w / 2, layout.elevation(r.floorIndex, explode) + 0.2, r.z + 1.5]}
              className="room-label"
              center
            >
              {r.id}
            </Html>
          ))}
    </group>
  );
}
