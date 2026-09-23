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
}
