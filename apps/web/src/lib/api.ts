import type {
  CommandInfo,
  EventLogDoc,
  FaultParams,
  FaultPreview,
  FaultSpec,
  LoggedEvent,
  PlantDesign,
  PlayResult,
  PointInfo,
  Value,
} from './types';

/** An export path in a URL: each segment percent-encoded, slashes kept. */
export function encodePath(path: string): string {
  return path.split('/').map(encodeURIComponent).join('/');
}

export interface AssetSummary {
  path: string;
  typeId: string;
  support: boolean;
  room: string | null;
  floor: string | null;
  system: string | null;
  role: string | null;
  pointCount: number;
}

async function get<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: ${response.status} ${response.statusText}`);
  return response.json() as Promise<T>;
}

/** POST a JSON body; a rejection throws with the twin's own explanation. */
async function post<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const error = (await response.json()) as { detail?: unknown };
      if (typeof error.detail === 'string') detail = error.detail;
      else if (error.detail !== undefined) detail = JSON.stringify(error.detail);
    } catch {
      // not JSON: keep the status line
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  plantDesign: () => get<PlantDesign>('/api/plant-design'),
  assets: () => get<AssetSummary[]>('/api/assets'),
  /** Every Asset Model point in registry (export) order, with its source class. */
  points: () => get<PointInfo[]>('/api/coverage/points'),
  faultCatalog: () => get<FaultSpec[]>('/api/faults/catalog'),
  /** Inject on exactly `target`, the asset the operator chose. */
  injectFault: (target: string, fault: string, params: FaultParams) =>
    post<LoggedEvent>('/api/faults', { target, fault, params }),
  clearFault: (target: string, fault: string) =>
    post<LoggedEvent>('/api/faults/clear', { target, fault }),
  previewFault: (target: string, fault: string, params: FaultParams, minutes: number) =>
    post<FaultPreview>('/api/faults/preview', { target, fault, params, minutes }),
  commands: (target: string) =>
    get<{ target: string; commands: CommandInfo[] }>(`/api/commands/${encodePath(target)}`),
  sendCommand: (target: string, command: string, value: Value) =>
    post<LoggedEvent>('/api/commands', { target, command, value }),
  events: () => get<{ time: number; events: LoggedEvent[] }>('/api/events'),
  reset: () => post<{ epoch: number }>('/api/reset', { confirm: true }),
  goldenDemo: () => get<EventLogDoc & { durationS: number }>('/api/golden-demo'),
  /** Reset the Live World and play the Golden Demo from steady state. */
  playGoldenDemo: () => post<PlayResult>('/api/golden-demo/play', { reset: true, confirm: true }),
  exportEvents: () => get<EventLogDoc>('/api/events/export'),
  /** Reset the Live World and replay an exported Event Log from steady state. */
  importEvents: (log: EventLogDoc) =>
    post<PlayResult>('/api/events/import', { log, reset: true, confirm: true }),
};
