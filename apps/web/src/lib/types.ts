/** Shapes served by the twin's REST and SSE surfaces (see docs/cli.md). */

export type ConnectionKind = 'power' | 'chw' | 'cw' | 'air' | 'water' | 'net' | 'fuel';

export interface Room {
  id: string;
  floor: string;
  name: string;
  /** Plan position of the room's corner and its size, in metres. */
  x: number;
  y: number;
  w: number;
  h: number;
  kind: string;
  fireZone: string | null;
  outdoor: boolean;
}

export interface PlacedAsset {
  /** Export path of an Asset, or `~<name>` for an Unexported Asset. */
  path: string;
  typeId: string;
  /** Null only for Support Assets, which are projected but not in the world. */
  room: string | null;
  x: number | null;
  y: number | null;
  role: string;
  system: string;
  unexported: boolean;
  support: boolean;
}

export interface UnexportedAsset {
  id: string;
  name: string;
  typeId: string;
  /** Plant View folders whose points observe this asset. */
  observedBy: string[];
}

export interface Connection {
  kind: ConnectionKind;
  /** Upstream node: an asset path, `~<name>` or a room id. */
  source: string;
  target: string;
  label: string;
}

export interface Shaft {
  id: string;
  name: string;
  /** Plan position of the shaft's centre, in metres. */
  x: number;
  y: number;
  /** Consecutive floors the shaft runs through, bottom to top. */
  floors: string[];
  /** Connection kinds that change floor in this shaft; each kind has at most one. */
  carries: ConnectionKind[];
}

export interface PlantDesign {
  version: string;
  floors: { name: string; index: number }[];
  rooms: Room[];
  assets: PlacedAsset[];
  unexported: UnexportedAsset[];
  connections: Connection[];
  shafts: Shaft[];
}

export type SourceClass =
  | 'command'
  | 'feedback'
  | 'process_value'
  | 'equipment_state'
  | 'fault_alarm'
  | 'energy_integral'
  | 'network_state'
  | 'static_metadata'
  | 'support';

export interface PointInfo {
  path: string;
  sourceClass: SourceClass;
  /** A fault or alarm bit, active when true or nonzero; counts, codes and texts are not. */
  alarmBit: boolean;
}

export type Quality = 'good' | 'uncertain' | 'bad';
export type Value = number | boolean | string | null;

export interface Reading {
  value: Value;
  quality: Quality;
}

/** One `/api/stream` event: a `snapshot` carries every point, a `delta` only changes. */
export interface Frame {
  seq: number;
  epoch: number;
  time: number;
  timestamp: string;
  events: number;
  points: Record<string, Reading>;
  /** Every active fault, in injection order. */
  faults?: ActiveFault[];
}

export type FaultCategory = 'equipment' | 'sensor' | 'communication' | 'control' | 'external';

/** A `/api/faults/catalog` entry: a failure mechanism bound to one asset type. */
export interface FaultSpec {
  id: string;
  name: string;
  assetType: string;
  /** The only assets of that type it applies to (a utility loss needs an incomer); null for all. */
  targets?: string[] | null;
  category: FaultCategory;
  mechanism: string;
  variable: string;
  span: number;
  unit: string;
  description: string;
  defaultSeverity: number;
}

/** Event params of a fault injection; see FaultParams in the twin. */
export interface FaultParams {
  severity: number;
  /** 0 is a step onset. */
  ramp_min: number;
  /** Null acts until cleared. */
  auto_clear_min: number | null;
}

/** One active fault, as every stream frame and `/api/faults` carry it. */
export interface ActiveFault {
  key: string;
  fault: string;
  name: string;
  category: FaultCategory;
  target: string;
  severity: number;
  /** Current level: rises from 0 to `severity` over the ramp. */
  level: number;
  since: number;
  rampMin: number;
  /** Sim time it clears itself, or null. */
  until: number | null;
}

export interface FaultPreview {
  target: string;
  fault: string;
  params: FaultParams;
  minutes: number;
  start: number;
  end: number;
  timestamp: string;
  /** Plant Design nodes in propagation order. */
  affected: { node: string; firstAt: number; afterS: number; hops: number | null }[];
  diffs: {
    path: string;
    node: string | null;
    base: Value;
    predicted: Value;
    baseQuality: Quality;
    predictedQuality: Quality;
  }[];
  alarms: {
    path: string;
    node: string | null;
    base: Value;
    predicted: Value;
    firstAt: number;
    afterS: number;
  }[];
}

/** An Operator Command an asset takes, with its current value. */
export interface CommandInfo {
  name: string;
  label: string;
  kind: 'choice' | 'switch' | 'number';
  choices: string[];
  minimum: number | null;
  maximum: number | null;
  unit: string;
  value: Value;
}

/** An Event Log entry. */
export interface LoggedEvent {
  at: number;
  timestamp: string;
  kind: string;
  target: string;
  params: Record<string, unknown>;
}
