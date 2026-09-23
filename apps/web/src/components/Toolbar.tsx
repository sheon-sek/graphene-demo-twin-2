import { showFloor, showRoom, showSite, locate } from '../actions';
import { contextOf } from '../lib/camera';
import type { World } from '../lib/world';
import { useConsole } from '../store';

/** Camera presets as a site → floor → room → asset trail, plus the floor stack controls. */
export function Toolbar({ world }: { world: World }) {
  const selected = useConsole((s) => s.selected);
  const view = useConsole((s) => s.view);
  const explodeTarget = useConsole((s) => s.explodeTarget);
  const setExplodeTarget = useConsole((s) => s.setExplodeTarget);
  const cutaway = useConsole((s) => s.cutaway);
  const setCutaway = useConsole((s) => s.setCutaway);
  const { layout } = world;

  const framed =
    view.kind === 'floor'
      ? { floor: view.floor, room: null }
      : view.kind === 'room'
        ? { floor: layout.rooms.get(view.room)?.floor ?? null, room: view.room }
        : view.kind === 'asset'
          ? contextOf(view.path, layout)
          : { floor: null, room: null };
  const trail = selected !== null && layout.assets.has(selected) ? contextOf(selected, layout) : framed;
  const floorRooms = trail.floor
    ? [...layout.rooms.values()].filter((r) => r.floor === trail.floor)
    : [];

  return (
    <nav className="toolbar" aria-label="Camera">
      <div className="crumbs">
        <button className={view.kind === 'site' ? 'on' : ''} onClick={showSite}>
          Site
        </button>
        {trail.floor && (
          <>
            <span className="sep">›</span>
            <button
              className={view.kind === 'floor' ? 'on' : ''}
              onClick={() => showFloor(world, trail.floor!)}
            >
              {trail.floor}
            </button>
          </>
        )}
        {trail.floor && (
          <>
            <span className="sep">›</span>
            <select
              aria-label="Room"
              value={trail.room ?? ''}
              onChange={(e) => e.target.value && showRoom(world, e.target.value)}
            >
              <option value="">Room…</option>
              {floorRooms.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.id} · {r.name}
                </option>
              ))}
            </select>
          </>
        )}
        {selected !== null && layout.assets.has(selected) && (
          <>
            <span className="sep">›</span>
            <button
              className={view.kind === 'asset' ? 'on' : ''}
              onClick={() => locate(world, selected)}
              title={selected}
            >
              {selected.slice(selected.lastIndexOf('/') + 1)}
            </button>
          </>
        )}
      </div>
      <div className="floors" role="group" aria-label="Floors">
        {[...layout.floors].reverse().map((f) => (
          <button
            key={f.name}
            className={view.kind === 'floor' && view.floor === f.name ? 'on' : ''}
            onClick={() => showFloor(world, f.name)}
          >
            {f.name}
          </button>
        ))}
      </div>
      <div className="stack">
        <button
          onClick={() => setExplodeTarget(explodeTarget > 0.5 ? 0 : 1)}
          aria-pressed={explodeTarget > 0.5}
        >
          {explodeTarget > 0.5 ? 'Collapse stack' : 'Explode stack'}
        </button>
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={explodeTarget}
          aria-label="Floor separation"
          onChange={(e) => setExplodeTarget(Number(e.target.value))}
        />
        {cutaway !== null && <button onClick={() => setCutaway(null)}>Show all floors</button>}
      </div>
    </nav>
  );
}
