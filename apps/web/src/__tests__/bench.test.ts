import { describe, expect, it } from 'vitest';
import { benchSettings, summarizeFrames } from '../lib/bench';

describe('frame-rate benchmark', () => {
  it('reports the median and the 5th-percentile (slow-frame) frame rate', () => {
    // 90 frames at 60 fps and 10 at 20 fps.
    const intervals = [...Array(90).fill(1000 / 60), ...Array(10).fill(50)];
    const summary = summarizeFrames(intervals);
    expect(summary.frames).toBe(100);
    expect(summary.medianFps).toBeCloseTo(60);
    expect(summary.p5Fps).toBeCloseTo(20);
    expect(summary.seconds).toBeCloseTo(90 / 60 + 0.5);
  });

  it('is empty when no frame was drawn', () => {
    expect(summarizeFrames([])).toEqual({ frames: 0, seconds: 0, medianFps: 0, p5Fps: 0 });
  });

  it('is off unless ?bench is given, with a warmup and a fixed window by default', () => {
    expect(benchSettings('')).toBeNull();
    expect(benchSettings('?e2e')).toBeNull();
    expect(benchSettings('?bench')).toEqual({ warmupS: 5, windowS: 30 });
    expect(benchSettings('?bench&warmup=1&window=2')).toEqual({ warmupS: 1, windowS: 2 });
  });
});
