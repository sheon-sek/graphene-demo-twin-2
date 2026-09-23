import { useFrame, useThree } from '@react-three/fiber';
import { useEffect, useRef } from 'react';
import { Matrix4, Vector3 } from 'three';
import { locate } from '../actions';
import { familyFocus, familyOf } from '../lib/silhouettes';
import type { World } from '../lib/world';
import { useConsole } from '../store';

export interface Perf {
  fps: number;
  drawCalls: number;
  triangles: number;
}

/** Hooks the smoke test (and a curious operator's devtools) can use. */
export interface TwinHandle {
  perf: Perf;
  /** CSS pixel position of an asset on screen, or null when it is off screen or not placed. */
  screenOf(node: string): { x: number; y: number } | null;
  locate(node: string): void;
  /** True once the camera and the floor stack have stopped moving. */
  settled(): boolean;
}

declare global {
  interface Window {
    __twin?: TwinHandle;
  }
}

const v = new Vector3();

/** Measures frame rate and draw calls once a second, and exposes `window.__twin`. */
export function Probe({ world, onPerf }: { world: World; onPerf: (perf: Perf) => void }) {
  const { gl, camera } = useThree();
  const frames = useRef(0);
  const since = useRef(performance.now());
  const perf = useRef<Perf>({ fps: 0, drawCalls: 0, triangles: 0 });
  const lastPose = useRef(new Matrix4());
  const stillFrames = useRef(0);

  useFrame(() => {
    frames.current++;
    const moved = !lastPose.current.equals(camera.matrixWorld);
    stillFrames.current = moved ? 0 : stillFrames.current + 1;
    lastPose.current.copy(camera.matrixWorld);
    const now = performance.now();
    if (now - since.current >= 1000) {
      perf.current = {
        fps: Math.round((frames.current * 1000) / (now - since.current)),
        drawCalls: gl.info.render.calls,
        triangles: gl.info.render.triangles,
      };
      frames.current = 0;
      since.current = now;
      onPerf(perf.current);
    }
  });

  useEffect(() => {
    window.__twin = {
      get perf() {
        return perf.current;
      },
      screenOf(node) {
        const spot = world.layout.assets.get(node);
        if (!spot) return null;
        const { explode } = useConsole.getState();
        const focus = familyFocus(familyOf(spot.typeId));
        v.set(spot.x, world.layout.elevation(spot.floorIndex, explode) + focus, spot.z);
        v.project(camera);
        if (Math.abs(v.x) > 1 || Math.abs(v.y) > 1 || v.z > 1) return null;
        const rect = gl.domElement.getBoundingClientRect();
        return {
          x: rect.left + ((v.x + 1) / 2) * rect.width,
          y: rect.top + ((1 - v.y) / 2) * rect.height,
        };
      },
      locate: (node) => locate(world, node),
      settled() {
        const s = useConsole.getState();
        return stillFrames.current >= 3 && s.explode === s.explodeTarget;
      },
    };
    return () => {
      delete window.__twin;
    };
  }, [world, gl, camera]);

  return null;
}
