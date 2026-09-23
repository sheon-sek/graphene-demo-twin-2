import { useMemo, useState, useSyncExternalStore } from 'react';
import { locate } from '../actions';
import { formatValue } from '../lib/format';
import { neighbours } from '../lib/graph';
import type { LiveStore } from '../lib/live';
import { sparklinePath } from '../lib/sparkline';
import { assetStatus } from '../lib/status';
import type { World } from '../lib/world';
import { useConsole } from '../store';
import { ControlTab } from './ControlTab';
import { FaultsTab } from './FaultsTab';

const TABS = ['Points', 'Connections', 'Control', 'Faults'] as const;
type Tab = (typeof TABS)[number];

/** Right-hand inspector for the selected asset. */
export function Inspector({ world, live }: { world: World; live: LiveStore }) {
  const selected = useConsole((s) => s.selected);
  const [tab, setTab] = useState<Tab>('Points');
  if (selected === null) {
    return (
      <aside className="inspector empty">
        <p>Select an asset in the building or the tree.</p>
      </aside>
    );
  }
  const node = world.nodes.get(selected);
  const unexported = world.design.unexported.find((u) => u.id === selected);
  return (
    <aside className="inspector">
      <header>
        <h2 title={selected}>{unexported?.name ?? selected}</h2>
        <div className="meta">
          <span>{node?.typeId}</span>
          {node?.room && <span>{node.room}</span>}
          {node?.unexported && <span className="tag unexported">Unexported Asset</span>}
          {node?.support && <span className="tag">Support Asset</span>}
          <LiveStatus world={world} live={live} node={selected} />
        </div>
        {node?.role && <p className="role">{node.role}</p>}
        {unexported && (
          <p className="role">Observed through {unexported.observedBy.join(', ')}</p>
        )}
      </header>
      <div role="tablist" className="tabs">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </div>
      {tab === 'Points' && <PointsTab world={world} live={live} node={selected} />}
      {tab === 'Connections' && <ConnectionsTab world={world} node={selected} />}
      {tab === 'Control' && <ControlTab key={selected} live={live} node={selected} />}
      {tab === 'Faults' && <FaultsTab key={selected} world={world} live={live} node={selected} />}
    </aside>
  );
}

function useLive(live: LiveStore): number {
  return useSyncExternalStore(
    (listener) => live.subscribe(listener),
    () => live.version,
  );
}

function LiveStatus({ world, live, node }: { world: World; live: LiveStore; node: string }) {
  useLive(live);
  const status = assetStatus(
    world.pointsOf(node).map((p) => ({ ...p, reading: live.reading(p.path) })),
  );
  return <span className={`status ${status}`}>{status}</span>;
}

function PointsTab({ world, live, node }: { world: World; live: LiveStore; node: string }) {
  useLive(live);
  const points = world.pointsOf(node);
  if (!points.length) return <p className="hint pad">No points observe this node.</p>;
  const owner = node.startsWith('~') ? null : node;
  const time = live.timestamp.slice(11, 19);
  return (
    <table className="points">
      <thead>
        <tr>
          <th>Point</th>
          <th>Value</th>
          <th>Quality</th>
          <th>Time</th>
          <th>Trend</th>
        </tr>
      </thead>
      <tbody>
        {points.map((p) => {
          const reading = live.reading(p.path);
          const name = owner ? p.path.slice(owner.length + 1) : p.path;
          return (
            <tr key={p.path} aria-label={name} title={`${p.path} (${p.sourceClass})`}>
              <td className="name">{name}</td>
              <td className="value">{reading ? formatValue(reading.value) : '…'}</td>
              <td>
                {reading && <span className={`quality ${reading.quality}`}>{reading.quality}</span>}
              </td>
              <td className="time">{reading ? time : ''}</td>
              <td>
                <svg width={72} height={18} viewBox="-1 -1 74 20" className="spark">
                  <path d={sparklinePath(live.history(p.path), 72, 18)} />
                </svg>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function ConnectionsTab({ world, node }: { world: World; node: string }) {
  const { upstream, downstream } = useMemo(() => neighbours(world.design, node), [world, node]);
  const section = (title: string, cls: string, byKind: typeof upstream) => (
    <section className={`neighbours ${cls}`}>
      <h3>{title}</h3>
      {Object.keys(byKind).length === 0 && <p className="hint">None in the Plant Design.</p>}
      {Object.entries(byKind).map(([kind, nodes]) => (
        <div key={kind}>
          <h4>{kind}</h4>
          {nodes!.map((n) =>
            world.nodes.has(n) ? (
              <button key={n} className="link" onClick={() => locate(world, n)}>
                {n}
              </button>
            ) : (
              <span key={n} className="room-ref">
                {world.layout.rooms.get(n)?.name ?? n}
              </span>
            ),
          )}
        </div>
      ))}
    </section>
  );
  return (
    <div className="connections">
      {section('Upstream', 'up', upstream)}
      {section('Downstream', 'down', downstream)}
    </div>
  );
}
