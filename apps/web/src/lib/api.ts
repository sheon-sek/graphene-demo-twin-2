import type { PlantDesign, PointInfo } from './types';

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

export const api = {
  plantDesign: () => get<PlantDesign>('/api/plant-design'),
  assets: () => get<AssetSummary[]>('/api/assets'),
  /** Every Asset Model point in registry (export) order, with its source class. */
  points: () => get<PointInfo[]>('/api/coverage/points'),
};
