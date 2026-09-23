import { useEffect, useState, useSyncExternalStore } from 'react';
import { AssetTree } from './components/AssetTree';
import { Inspector } from './components/Inspector';
import { Layers, Legend } from './components/Legend';
import { SearchBox } from './components/SearchBox';
import { Toolbar } from './components/Toolbar';
import { api } from './lib/api';
import { LiveStore } from './lib/live';
import { connectStream, type StreamState } from './lib/stream';
import { buildWorld, type World } from './lib/world';
import { Scene } from './scene/Scene';
import type { Perf } from './scene/Probe';
import { useConsole } from './store';

const live = new LiveStore(120);

export function App() {
  const [world, setWorld] = useState<World | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stream, setStream] = useState<StreamState>('connecting');
  const [perf, setPerf] = useState<Perf | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.plantDesign(), api.assets(), api.points()])
      .then(([design, assets, points]) => {
        if (!cancelled)
          setWorld(
            buildWorld(
              design,
              assets.map((a) => a.path),
              points,
            ),
          );
      })
      .catch((e: unknown) => setError(String(e)));
    const close = connectStream(live, setStream);
    return () => {
      cancelled = true;
      close();
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !(e.target instanceof HTMLInputElement)) {
        useConsole.getState().select(null);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  if (error) return <div className="splash error">Cannot load the twin: {error}</div>;
  if (!world) return <div className="splash">Loading the Plant Design…</div>;

  return (
    <div className="app">
      <header className="topbar">
        <h1>Graphene Operator Console</h1>
        <Toolbar world={world} />
      </header>
      <aside className="left">
        <SearchBox world={world} />
        <Layers />
        <AssetTree world={world} />
      </aside>
      <main className="stage" data-testid="stage">
        <Scene world={world} live={live} onPerf={setPerf} />
        <Legend />
        <Hovered />
      </main>
      <Inspector world={world} live={live} />
      <StatusBar stream={stream} perf={perf} />
    </div>
  );
}

function Hovered() {
  const hovered = useConsole((s) => s.hovered);
  return hovered ? <div className="hovered">{hovered}</div> : null;
}

function StatusBar({ stream, perf }: { stream: StreamState; perf: Perf | null }) {
  useSyncExternalStore(
    (l) => live.subscribe(l),
    () => live.version,
  );
  return (
    <footer className="statusbar">
      <span className={`stream ${stream}`}>{stream}</span>
      <span>Live World {live.timestamp ? live.timestamp.replace('T', ' ').replace('Z', ' UTC') : '—'}</span>
      <span>epoch {live.epoch < 0 ? '—' : live.epoch}</span>
      <span>step {live.seq < 0 ? '—' : live.seq}</span>
      <span>Event Log {live.eventCount}</span>
      {perf && (
        <span className="perf" data-testid="perf">
          {perf.fps} fps · {perf.drawCalls} draws · {Math.round(perf.triangles / 1000)}k tris
        </span>
      )}
    </footer>
  );
}
