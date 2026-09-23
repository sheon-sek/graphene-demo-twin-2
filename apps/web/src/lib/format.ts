import type { Value } from './types';

export function formatValue(value: Value): string {
  if (typeof value === 'number')
    return Number.isInteger(value) ? String(value) : String(+value.toFixed(3));
  if (value === null) return '—';
  return String(value);
}

/** The last segment of an export path (`CRAC/L1_CRAC3` → `L1_CRAC3`). */
export function shortName(path: string): string {
  return path.slice(path.lastIndexOf('/') + 1);
}

/** `hh:mm:ss` of an ISO timestamp, in UTC like sim time. */
export function clockTime(timestamp: string): string {
  return timestamp.slice(11, 19);
}
