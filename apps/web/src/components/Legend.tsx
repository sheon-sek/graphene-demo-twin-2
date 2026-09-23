import { LAYERS } from '../lib/routing';
import { useConsole } from '../store';
import {
  DOWNSTREAM_COLOR,
  FAULT_COLOR,
  LAYER_COLOR,
  STATUS_COLOR,
  UPSTREAM_COLOR,
} from '../scene/palette';

/** Connection layer toggles. */
export function Layers() {
  const layers = useConsole((s) => s.layers);
  const toggleLayer = useConsole((s) => s.toggleLayer);
  return (
    <fieldset className="layers">
      <legend>Layers</legend>
      {LAYERS.map((layer) => (
        <label key={layer}>
          <input type="checkbox" checked={layers[layer]} onChange={() => toggleLayer(layer)} />
          <i style={{ background: LAYER_COLOR[layer] }} />
          {layer}
        </label>
      ))}
    </fieldset>
  );
}

/** The state language drawn in the scene. */
export function Legend() {
  return (
    <div className="legend">
      {(['normal', 'warning', 'alarm', 'offline'] as const).map((s) => (
        <span key={s}>
          <i style={{ background: STATUS_COLOR[s] }} />
          {s}
        </span>
      ))}
      <span>
        <i className="hatched" style={{ background: STATUS_COLOR.bad }} />
        bad quality
      </span>
      <span>
        <i className="ghost" />
        unexported
      </span>
      <span>
        <i style={{ background: UPSTREAM_COLOR }} />
        upstream
      </span>
      <span>
        <i style={{ background: DOWNSTREAM_COLOR }} />
        downstream
      </span>
      <span>
        <i className="pulse" style={{ background: FAULT_COLOR }} />
        injected fault · causal path
      </span>
    </div>
  );
}
