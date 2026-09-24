import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { locate } from '../actions';
import { api } from '../lib/api';
import { clockTime, shortName } from '../lib/format';
import type { LiveStore } from '../lib/live';
import { isSet } from '../lib/status';
import type { LoggedEvent } from '../lib/types';
import type { World } from '../lib/world';
import { StoryControls } from './StoryControls';

/** Active faults with per-fault Clear, set alarm bits, the Event Log timeline, and Reset. */
export function BottomBar({ world, live }: { world: World; live: LiveStore }) {
  useSyncExternalStore(
    (l) => live.subscribe(l),
    () => live.version,
  );
  const [error, setError] = useState<string | null>(null);
  const fail = useCallback(
    (e: unknown) => setError(e instanceof Error ? e.message : String(e)),
    [],
  );
  const alarms = world.alarmPoints.filter((p) => isSet(live.reading(p.path)?.value ?? null));

  return (
    <section className="bottombar" aria-label="Operations">
      <section className="panel" aria-label="Active faults">
        <h3>
          Active faults <span className="count">{live.faults.length}</span>
        </h3>
        {live.faults.length === 0 && <p className="hint">None: the world follows the Base World.</p>}
        <ul>
          {live.faults.map((f) => (
            <li key={f.key} className="fault-row">
              <i className="marker" />
              <button className="link" onClick={() => locate(world, f.target)} title={f.target}>
                {f.name} · {shortName(f.target)}
              </button>
              <span className="level">{Math.round(f.level * 100)} %</span>
              <button
                aria-label={`Clear ${f.name} on ${f.target}`}
                onClick={() => api.clearFault(f.target, f.fault).catch(fail)}
              >
                Clear
              </button>
            </li>
          ))}
        </ul>
      </section>
      <section className="panel" aria-label="Alarms">
        <h3>
          Alarms <span className="count">{alarms.length}</span>
        </h3>
        <ul>
          {alarms.map((p) => {
            const owner = world.ownerOf(p.path);
            return (
              <li key={p.path}>
                <button
                  className="link alarm"
                  title={p.path}
                  onClick={() => owner && locate(world, owner)}
                >
                  {owner ? `${shortName(owner)} · ${p.path.slice(owner.length + 1)}` : p.path}
                </button>
              </li>
            );
          })}
        </ul>
      </section>
      <EventLog live={live} onError={fail} />
      <div className="controls">
        <StoryControls live={live} onError={fail} />
        <ResetControl live={live} onError={fail} />
      </div>
      {error && (
        <p className="error bar-error" role="alert">
          {error}{' '}
          <button className="link" onClick={() => setError(null)}>
            dismiss
          </button>
        </p>
      )}
    </section>
  );
}

function EventLog({ live, onError }: { live: LiveStore; onError: (e: unknown) => void }) {
  const [events, setEvents] = useState<LoggedEvent[]>([]);
  const { eventCount, epoch } = live;
  useEffect(() => {
    let cancelled = false;
    api
      .events()
      .then((body) => !cancelled && setEvents(body.events))
      .catch((e: unknown) => !cancelled && onError(e));
    return () => {
      cancelled = true;
    };
  }, [eventCount, epoch, onError]);
  return (
    <section className="panel timeline" aria-label="Event Log">
      <h3>
        Event Log <span className="count">{events.length}</span>
      </h3>
      <ol reversed>
        {[...events].reverse().map((e, i) => (
          <li
            key={events.length - i}
            className={e.at > live.time ? 'pending' : undefined}
            title={JSON.stringify(e.params)}
          >
            <span className="at">{clockTime(e.timestamp)}</span>
            <span className={`kind ${e.kind.replace('.', '-')}`}>{e.kind}</span>
            <span className="target">{shortName(e.target)}</span>
            <span className="params">{summary(e)}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function summary(e: LoggedEvent): string {
  const p = e.params;
  if (e.kind === 'command') return `${p.command} = ${String(p.value)}`;
  const extra = e.kind === 'fault.inject' && p.severity !== undefined ? ` @ ${p.severity}` : '';
  return `${String(p.fault ?? '')}${extra}`;
}

function ResetControl({ live, onError }: { live: LiveStore; onError: (e: unknown) => void }) {
  const [confirming, setConfirming] = useState(false);
  if (!confirming)
    return (
      <div className="reset">
        <button className="danger" onClick={() => setConfirming(true)}>
          Reset…
        </button>
      </div>
    );
  const n = live.eventCount;
  return (
    <div className="reset confirming" role="alertdialog" aria-label="Confirm Reset">
      <p>
        Reset discards the Event Log ({n} event{n === 1 ? '' : 's'}) and every What-if Fork, and
        rebuilds the Live World from its initial state, as a restart would.
      </p>
      <button
        className="danger"
        onClick={() => {
          setConfirming(false);
          api.reset().catch(onError);
        }}
      >
        Confirm reset
      </button>
      <button onClick={() => setConfirming(false)}>Cancel</button>
    </div>
  );
}
