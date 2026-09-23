/**
 * Frame-rate benchmark (`?bench`): every asset and layer on screen, a warmup, then the frame
 * intervals over a fixed window. Run by hand on the target laptop; CI keeps the draw-call budget.
 */
export interface BenchSettings {
  warmupS: number;
  windowS: number;
}

export interface BenchSummary {
  frames: number;
  seconds: number;
  medianFps: number;
  /** Frame rate of the slowest 5 % of frames: the rate the scene sustains. */
  p5Fps: number;
}

/** `?bench`, optionally with `warmup=` and `window=` in seconds; null when not benchmarking. */
export function benchSettings(search: string): BenchSettings | null {
  const params = new URLSearchParams(search);
  if (!params.has('bench')) return null;
  const seconds = (key: string, fallback: number) => {
    const value = Number(params.get(key) ?? NaN);
    return Number.isFinite(value) && value > 0 ? value : fallback;
  };
  return { warmupS: seconds('warmup', 5), windowS: seconds('window', 30) };
}

export function summarizeFrames(intervalsMs: number[]): BenchSummary {
  if (intervalsMs.length === 0) return { frames: 0, seconds: 0, medianFps: 0, p5Fps: 0 };
  const sorted = [...intervalsMs].sort((a, b) => a - b);
  const quantile = (q: number) => sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];
  return {
    frames: sorted.length,
    seconds: sorted.reduce((sum, ms) => sum + ms, 0) / 1000,
    medianFps: 1000 / quantile(0.5),
    p5Fps: 1000 / quantile(0.95),
  };
}
