import { describe, expect, it } from 'vitest';
import { cameraFor, contextOf } from '../lib/camera';
import { buildLayout } from '../lib/layout';
import { plantDesign } from './fixtures';

const design = plantDesign();
const layout = buildLayout(design);

function distance(v: { position: number[]; target: number[] }): number {
  return Math.hypot(...v.position.map((p, i) => p - v.target[i]));
}

describe('camera presets', () => {
  it('zooms in from site to floor to room to asset', () => {
    const views = [
      cameraFor({ kind: 'site' }, layout, 1),
      cameraFor({ kind: 'floor', floor: 'Roof' }, layout, 1),
      cameraFor({ kind: 'room', room: 'R-CHP' }, layout, 1),
      cameraFor({ kind: 'asset', path: 'Chiller/R_C1' }, layout, 1),
    ];
    for (let i = 1; i < views.length; i++) {
      expect(distance(views[i])).toBeLessThan(distance(views[i - 1]));
    }
  });

  it('aims at the thing it frames, on its exploded floor', () => {
    const asset = layout.assets.get('Chiller/R_C1')!;
    const view = cameraFor({ kind: 'asset', path: 'Chiller/R_C1' }, layout, 1);
    expect(view.target[0]).toBeCloseTo(asset.x);
    expect(view.target[2]).toBeCloseTo(asset.z);
    expect(view.target[1]).toBeGreaterThan(layout.elevation(3, 1));

    const hall = layout.rooms.get('DH03')!;
    const room = cameraFor({ kind: 'room', room: 'DH03' }, layout, 1);
    expect(room.target[0]).toBeCloseTo(hall.x + hall.w / 2);
    expect(room.target[2]).toBeCloseTo(hall.z + hall.d / 2);
  });

  it('frames the floor from above its own level', () => {
    const l1 = cameraFor({ kind: 'floor', floor: 'Level 1' }, layout, 1);
    expect(l1.target[1]).toBeCloseTo(layout.elevation(1, 1));
    expect(l1.position[1]).toBeGreaterThan(l1.target[1]);
  });

  it('gives the site → floor → room → asset trail of a selection', () => {
    expect(contextOf('Chiller/R_C1', layout)).toEqual({ floor: 'Roof', room: 'R-CHP' });
    expect(contextOf('Dashboard/Nope', layout)).toEqual({ floor: null, room: null });
  });
});
