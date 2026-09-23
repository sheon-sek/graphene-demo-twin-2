import { CameraControls } from '@react-three/drei';
import { useFrame } from '@react-three/fiber';
import { useEffect, useRef } from 'react';
import { cameraFor } from '../lib/camera';
import type { World } from '../lib/world';
import { useConsole } from '../store';

/** `?e2e` makes camera moves and floor separation instant, for deterministic captures. */
const instant = new URLSearchParams(window.location.search).has('e2e');

/** Flies the camera to the requested view; the operator can orbit, pan and zoom freely. */
export function CameraRig({ world }: { world: World }) {
  const controls = useRef<CameraControls>(null);
  const view = useConsole((s) => s.view);
  const explodeTarget = useConsole((s) => s.explodeTarget);
  const first = useRef(true);

  useEffect(() => {
    const pose = cameraFor(view, world.layout, explodeTarget);
    controls.current?.setLookAt(...pose.position, ...pose.target, !first.current && !instant);
    first.current = false;
    // Re-fly when the same view is asked for again (nonce) or the stack changes height.
  }, [view, view.nonce, explodeTarget, world]);

  return (
    <CameraControls
      ref={controls}
      makeDefault
      minDistance={3}
      maxDistance={600}
      maxPolarAngle={Math.PI * 0.49}
      smoothTime={0.35}
    />
  );
}

/** Eases the drawn floor separation toward the requested one. */
export function ExplodeDriver() {
  useFrame((_, dt) => {
    const { explode, explodeTarget, setExplode } = useConsole.getState();
    if (explode === explodeTarget) return;
    const step = Math.min(1, dt * 4) * (explodeTarget - explode);
    const done = instant || Math.abs(explodeTarget - explode) < 0.005;
    setExplode(done ? explodeTarget : explode + step);
  });
  return null;
}
