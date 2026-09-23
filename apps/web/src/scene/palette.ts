import type { Layer } from '../lib/routing';
import type { Status } from '../lib/status';
import type { ConnectionKind } from '../lib/types';

/** The console's state language, shared by the 3D scene, the inspector and the legend. */
export const STATUS_COLOR: Record<Status, string> = {
  normal: '#9fc4b4',
  warning: '#f5b83d',
  alarm: '#ef4444',
  offline: '#3f4b5c',
  bad: '#8b9099',
  unknown: '#64748b',
};

export const KIND_COLOR: Record<ConnectionKind, string> = {
  power: '#f59e0b',
  fuel: '#b7791f',
  chw: '#3b82f6',
  cw: '#14b8a6',
  air: '#a5d8ff',
  water: '#4ade80',
  net: '#c084fc',
};

export const LAYER_COLOR: Record<Layer, string> = {
  Electrical: KIND_COLOR.power,
  Hydraulic: KIND_COLOR.chw,
  Airside: KIND_COLOR.air,
  Network: KIND_COLOR.net,
  Water: KIND_COLOR.water,
};

export const UPSTREAM_COLOR = '#22d3ee';
export const DOWNSTREAM_COLOR = '#f472b6';
export const DIMMED_COLOR = '#1c232c';
export const SELECTED_COLOR = '#ffffff';

export const ROOM_COLOR: Record<string, string> = {
  hall: '#1b2636',
  electrical: '#2a2616',
  cooling: '#132a3d',
  airside: '#12302f',
  water: '#10301f',
  fuel: '#2e2012',
  core: '#26262b',
  support: '#1f2430',
  corridor: '#1a1d22',
};
