import type { LiveStore } from './live';
import type { Frame } from './types';

export type StreamState = 'connecting' | 'live' | 'reconnecting';

/**
 * Feed the Live World's `/api/stream` into `live`. The browser reconnects on its own, and the
 * server opens every connection with a snapshot, so a reconnect resynchronises everything.
 */
export function connectStream(live: LiveStore, onState: (state: StreamState) => void): () => void {
  const source = new EventSource('/api/stream');
  onState('connecting');
  const apply = (kind: 'snapshot' | 'delta') => (e: MessageEvent<string>) =>
    live.apply(kind, JSON.parse(e.data) as Frame);
  source.addEventListener('snapshot', apply('snapshot'));
  source.addEventListener('delta', apply('delta'));
  source.onopen = () => onState('live');
  source.onerror = () => onState('reconnecting');
  return () => source.close();
}
