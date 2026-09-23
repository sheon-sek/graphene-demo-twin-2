import { useFrame } from '@react-three/fiber';
import { useEffect, useRef } from 'react';
import { showSite } from '../actions';
import { summarizeFrames, type BenchSettings, type BenchSummary } from '../lib/bench';
import { LAYERS } from '../lib/routing';
import { useConsole } from '../store';

export type BenchState =
  | { phase: 'warmup' | 'measuring'; settings: BenchSettings }
  | { phase: 'done'; settings: BenchSettings; summary: BenchSummary };

/**
 * Shows the whole site with every asset and layer, warms up, then records every frame interval
 * over a fixed window and reports the median and 5th-percentile frame rate.
 */
export function Bench({
  settings,
  onState,
}: {
  settings: BenchSettings;
  onState: (state: BenchState) => void;
}) {
  const started = useRef(performance.now());
  const last = useRef<number | null>(null);
  const intervals = useRef<number[]>([]);
  const phase = useRef<BenchState['phase']>('warmup');

  useEffect(() => {
    const s = useConsole.getState();
    s.select(null);
    for (const layer of LAYERS) if (!s.layers[layer]) s.toggleLayer(layer);
    s.setExplodeTarget(1);
    showSite();
    onState({ phase: 'warmup', settings });
  }, [settings, onState]);

  useFrame(() => {
    if (phase.current === 'done') return;
    const now = performance.now();
    const elapsed = (now - started.current) / 1000;
    if (phase.current === 'warmup') {
      if (elapsed < settings.warmupS) return;
      phase.current = 'measuring';
      onState({ phase: 'measuring', settings });
    } else {
      intervals.current.push(now - last.current!);
    }
    last.current = now;
    if (elapsed >= settings.warmupS + settings.windowS) {
      phase.current = 'done';
      const summary = summarizeFrames(intervals.current);
      console.log(`bench: ${JSON.stringify(summary)}`);
      onState({ phase: 'done', settings, summary });
    }
  });

  return null;
}
