import { useFrame } from '@react-three/fiber';
import { useRef, useSyncExternalStore } from 'react';
import { AdditiveBlending, type Group } from 'three';
import type { LiveStore } from '../lib/live';
import { familyGeometry, familyOf } from '../lib/silhouettes';
import type { World } from '../lib/world';
import { useConsole } from '../store';
import { FAULT_COLOR } from './palette';

/** A pulsing ring and beacon on every asset with an injected fault, apart from the state colour. */
export function FaultMarkers({ world, live }: { world: World; live: LiveStore }) {
  const targets = useSyncExternalStore(
    (l) => live.subscribe(l),
    () => live.faultTargets,
  );
  const explode = useConsole((s) => s.explode);
  const cutaway = useConsole((s) => s.cutaway);
  const markers = useRef<Group>(null);

  useFrame(({ clock }) => {
    const pulse = 0.5 + 0.5 * Math.sin(clock.elapsedTime * 5);
    markers.current?.children.forEach((marker) => {
      const [ring, beacon] = marker.children;
      const s = 1 + 0.35 * pulse;
      ring.scale.set(s, s, 1);
      beacon.scale.set(1, 0.6 + 0.4 * pulse, 1);
    });
  });

  return (
    <group ref={markers}>
      {(targets ? targets.split('\n') : []).map((target) => {
        const spot = world.layout.assets.get(target);
        if (!spot || (cutaway !== null && spot.floorIndex > cutaway)) return null;
        const radius = Math.max(0.9, (familyGeometry(familyOf(spot.typeId)).boundingSphere?.radius ?? 1) * 1.3);
        const y = world.layout.elevation(spot.floorIndex, explode);
        return (
          <group key={target} position={[spot.x, y, spot.z]}>
            <mesh rotation-x={-Math.PI / 2} position-y={0.18} raycast={() => null}>
              <ringGeometry args={[radius, radius + 0.35, 40]} />
              <meshBasicMaterial color={FAULT_COLOR} transparent opacity={0.9} toneMapped={false} />
            </mesh>
            <mesh position-y={3.5} raycast={() => null}>
              <cylinderGeometry args={[0.18, 0.18, 7, 8]} />
              <meshBasicMaterial
                color={FAULT_COLOR}
                transparent
                opacity={0.55}
                blending={AdditiveBlending}
                depthWrite={false}
                toneMapped={false}
              />
            </mesh>
          </group>
        );
      })}
    </group>
  );
}
