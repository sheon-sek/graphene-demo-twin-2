import { useState, useSyncExternalStore } from 'react';
import { locate, showRoom } from '../actions';
import { api } from '../lib/api';
import { formatValue, shortName } from '../lib/format';
import type { LiveStore } from '../lib/live';
import type { FaultParams, FaultPreview, FaultSpec } from '../lib/types';
import type { World } from '../lib/world';

const PREVIEW_MINUTES = [15, 30, 60] as const;
const DIFFS_SHOWN = 40;

/**
 * Catalog → parameters → Fault Preview → inject, for the selected asset only, plus the faults
 * already active on it. Keyed by the node, so switching assets starts afresh.
 */
export function FaultsTab({ world, live, node }: { world: World; live: LiveStore; node: string }) {
  const typeId = world.nodes.get(node)?.typeId;
  const specs = world.catalog.filter(
    (s) => s.assetType === typeId && (!s.targets || s.targets.includes(node)),
  );
  const [chosen, setChosen] = useState<FaultSpec | null>(null);
  const [severity, setSeverity] = useState(1);
  const [ramp, setRamp] = useState<number | null>(null);
  const [autoClear, setAutoClear] = useState<number | null>(null);
  /** The last preview and the fault and parameters it was run with. */
  const [preview, setPreview] = useState<{ asked: string; result: FaultPreview } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ error: boolean; text: string } | null>(null);

  const params = (): FaultParams => ({
    severity,
    ramp_min: ramp ?? 0,
    auto_clear_min: autoClear,
  });
  // A preview stands only for what it was run with: any other fault or parameters hide it.
  const asked = JSON.stringify([chosen?.id, params()]);
  const current = preview?.asked === asked ? preview.result : null;
  const choose = (spec: FaultSpec) => {
    setChosen(spec);
    setSeverity(spec.defaultSeverity);
    setPreview(null);
    setMessage(null);
  };
  const run = async (label: string, action: () => Promise<void>) => {
    setBusy(label);
    setMessage(null);
    try {
      await action();
    } catch (e) {
      setMessage({ error: true, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="faults-tab">
      <ActiveHere live={live} node={node} onError={(text) => setMessage({ error: true, text })} />
      {specs.length === 0 ? (
        <p className="hint">No faults in the catalog for {typeId ?? 'this node'}.</p>
      ) : (
        <fieldset className="catalog">
          <legend>Catalog for {typeId}</legend>
          {specs.map((s) => (
            <label key={s.id} className={chosen?.id === s.id ? 'on' : ''}>
              <input
                type="radio"
                name="fault"
                checked={chosen?.id === s.id}
                onChange={() => choose(s)}
              />
              <span className="fault-name">{s.name}</span>
              <span className={`category ${s.category}`}>{s.category}</span>
            </label>
          ))}
        </fieldset>
      )}
      {chosen && (
        <section className="fault-params" aria-label="Parameters">
          <p className="hint">{chosen.description}</p>
          <label className="row">
            <span>Severity</span>
            <input
              type="range"
              aria-label="Severity"
              min={0.05}
              max={1}
              step={0.05}
              value={severity}
              onChange={(e) => setSeverity(Number(e.target.value))}
            />
            <output>{Math.round(severity * 100)} %</output>
          </label>
          <fieldset className="row">
            <legend>Onset</legend>
            <label>
              <input type="radio" checked={ramp === null} onChange={() => setRamp(null)} />
              Step
            </label>
            <label>
              <input type="radio" checked={ramp !== null} onChange={() => setRamp(5)} />
              Ramp
            </label>
            {ramp !== null && (
              <input
                type="number"
                aria-label="Ramp minutes"
                min={1}
                max={1440}
                value={ramp}
                onChange={(e) => setRamp(Number(e.target.value))}
              />
            )}
            {ramp !== null && <span className="hint">min</span>}
          </fieldset>
          <fieldset className="row">
            <legend>Duration</legend>
            <label>
              <input
                type="radio"
                checked={autoClear === null}
                onChange={() => setAutoClear(null)}
              />
              Until cleared
            </label>
            <label>
              <input
                type="radio"
                checked={autoClear !== null}
                onChange={() => setAutoClear(30)}
              />
              Auto-clear
            </label>
            {autoClear !== null && (
              <input
                type="number"
                aria-label="Auto-clear minutes"
                min={1}
                max={1440}
                value={autoClear}
                onChange={(e) => setAutoClear(Number(e.target.value))}
              />
            )}
            {autoClear !== null && <span className="hint">min</span>}
          </fieldset>
          <div className="actions">
            {PREVIEW_MINUTES.map((m) => (
              <button
                key={m}
                disabled={busy !== null}
                onClick={() =>
                  run(`preview ${m}`, async () =>
                    setPreview({
                      asked,
                      result: await api.previewFault(node, chosen.id, params(), m),
                    }),
                  )
                }
              >
                Preview {m} min
              </button>
            ))}
            <button
              className="primary"
              disabled={busy !== null}
              onClick={() =>
                run('inject', async () => {
                  await api.injectFault(node, chosen.id, params());
                  setMessage({ error: false, text: `Injected ${chosen.name} on ${node}.` });
                })
              }
            >
              Inject
            </button>
          </div>
          {busy?.startsWith('preview') && <p className="hint">Running the What-if Fork…</p>}
        </section>
      )}
      {message && (
        <p role={message.error ? 'alert' : 'status'} className={message.error ? 'error' : 'ok'}>
          {message.text}
        </p>
      )}
      {preview && !current && (
        <p className="hint">The parameters changed since the last preview: preview again.</p>
      )}
      {current && current.target === node && <PreviewResult world={world} preview={current} />}
    </div>
  );
}

function ActiveHere({
  live,
  node,
  onError,
}: {
  live: LiveStore;
  node: string;
  onError: (text: string) => void;
}) {
  useSyncExternalStore(
    (l) => live.subscribe(l),
    () => live.version,
  );
  const here = live.faults.filter((f) => f.target === node);
  if (!here.length) return null;
  return (
    <section className="active-here" aria-label="Active faults on this asset">
      <h3>Active on this asset</h3>
      {here.map((f) => (
        <div key={f.key} className="fault-row">
          <i className="marker" />
          <span className="fault-name">{f.name}</span>
          <span className="level">{Math.round(f.level * 100)} %</span>
          <button
            aria-label={`Clear ${f.name}`}
            onClick={() => api.clearFault(f.target, f.fault).catch((e) => onError(String(e)))}
          >
            Clear
          </button>
        </div>
      ))}
    </section>
  );
}

function PreviewResult({ world, preview }: { world: World; preview: FaultPreview }) {
  const open = (node: string) =>
    world.nodes.has(node) ? locate(world, node) : showRoom(world, node);
  return (
    <section className="preview" aria-label="Fault Preview">
      <h3>
        Fault Preview · {preview.minutes} min · {preview.affected.length} affected
      </h3>
      <h4>Propagation</h4>
      <ol aria-label="Affected assets">
        {preview.affected.map((a) => (
          <li key={a.node}>
            <button className="link" onClick={() => open(a.node)} title={a.node}>
              {world.layout.rooms.has(a.node)
                ? `${a.node} · ${world.layout.rooms.get(a.node)!.name}`
                : shortName(a.node)}
            </button>
            <span className="after">+{duration(a.afterS)}</span>
          </li>
        ))}
      </ol>
      <h4>Alarm bits that would change</h4>
      {preview.alarms.length === 0 ? (
        <p className="hint">None.</p>
      ) : (
        <ul aria-label="Alarm changes">
          {preview.alarms.map((a) => (
            <li key={a.path} title={a.path}>
              <span className="name">{a.path}</span>
              <span className="change">
                {formatValue(a.base)} → {formatValue(a.predicted)}
              </span>
              <span className="after">+{duration(a.afterS)}</span>
            </li>
          ))}
        </ul>
      )}
      <h4>Point changes at +{preview.minutes} min</h4>
      <table className="diffs" aria-label="Point changes">
        <thead>
          <tr>
            <th>Point</th>
            <th>Without</th>
            <th>With fault</th>
          </tr>
        </thead>
        <tbody>
          {preview.diffs.slice(0, DIFFS_SHOWN).map((d) => (
            <tr key={d.path} title={d.path}>
              <td className="name">{d.path}</td>
              <td className={`quality-${d.baseQuality}`}>{formatValue(d.base)}</td>
              <td className={`quality-${d.predictedQuality}`}>
                {formatValue(d.predicted)}
                {d.predictedQuality !== 'good' && ` (${d.predictedQuality})`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {preview.diffs.length > DIFFS_SHOWN && (
        <p className="hint">…and {preview.diffs.length - DIFFS_SHOWN} more points.</p>
      )}
    </section>
  );
}

function duration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  return `${m}:${String(seconds % 60).padStart(2, '0')}`;
}
