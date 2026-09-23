import { Canvas } from '@react-three/fiber';
import type { LiveStore } from '../lib/live';
import type { World } from '../lib/world';
import { Assets } from './Assets';
import { Building } from './Building';
import { CameraRig, ExplodeDriver } from './CameraRig';
import { Connections } from './Connections';
import { FaultMarkers } from './FaultMarkers';
import { Probe, type Perf } from './Probe';

export function Scene({
  world,
  live,
  onPerf,
}: {
  world: World;
  live: LiveStore;
  onPerf: (perf: Perf) => void;
}) {
  return (
    <Canvas
      dpr={[1, 1.5]}
      camera={{ fov: 42, near: 0.5, far: 2000, position: [220, 160, 200] }}
      gl={{ antialias: true, powerPreference: 'high-performance' }}
    >
      <color attach="background" args={['#0b0f14']} />
      <hemisphereLight args={['#dbe7ff', '#1a1f26', 1.1]} />
      <directionalLight position={[80, 160, 60]} intensity={1.6} />
      <Building world={world} />
      <Assets world={world} live={live} />
      <FaultMarkers world={world} live={live} />
      <Connections world={world} live={live} />
      <ExplodeDriver />
      <CameraRig world={world} />
      <Probe world={world} onPerf={onPerf} />
    </Canvas>
  );
}
