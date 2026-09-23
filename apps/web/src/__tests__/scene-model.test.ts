import { describe, expect, it } from 'vitest';
import { buildSceneModel, nextPick } from '../lib/scene-model';
import { FAMILIES, familyFocus, familyGeometry, familyOf } from '../lib/silhouettes';
import { placedPaths, plantDesign } from './fixtures';

const design = plantDesign();

describe('silhouettes', () => {
  it('gives every placed UDT type a procedural silhouette of its own family', () => {
    const types = new Set(design.assets.filter((a) => a.room !== null).map((a) => a.typeId));
    for (const type of types) expect(familyOf(type), type).not.toBe('generic');
    expect(familyOf('Chiller')).toBe('chiller');
    expect(familyOf('Cooling Tower')).toBe('tower');
    expect(familyOf('Chiller Pump')).toBe('pump');
    expect(familyOf('Buffer Tank')).toBe('tank');
    expect(familyOf('Something New')).toBe('generic');
  });

  it('builds a low-poly geometry that stands on the floor for every family', () => {
    for (const family of FAMILIES) {
      const geometry = familyGeometry(family);
      const triangles = (geometry.index?.count ?? geometry.attributes.position.count) / 3;
      expect(triangles, family).toBeGreaterThan(0);
      expect(triangles, family).toBeLessThan(600);
      geometry.computeBoundingBox();
      expect(geometry.boundingBox!.min.y, family).toBeCloseTo(0);
      expect(geometry.boundingBox!.max.y, family).toBeGreaterThan(0);
    }
  });

  it('aims at the body of each silhouette, not the gap under a hung unit or a sensor head', () => {
    for (const family of FAMILIES) {
      const box = familyGeometry(family).boundingBox!;
      expect(familyFocus(family), family).toBeGreaterThan(box.min.y);
      expect(familyFocus(family), family).toBeLessThan(box.max.y);
    }
    expect(familyFocus('ceilingUnit')).toBeGreaterThan(3);
    expect(familyFocus('sensor')).toBeGreaterThan(1.5);
  });
});

describe('scene model', () => {
  const model = buildSceneModel(design);

  it('has one pickable instance per placed asset, and picking returns the asset', () => {
    const placed = placedPaths(design);
    expect(model.instances.size).toBe(placed.length);
    for (const path of placed) {
      const instance = model.instances.get(path)!;
      expect(instance, path).toBeDefined();
      expect(model.pick(instance.batch, instance.index)).toBe(path);
    }
  });

  it('draws Unexported Assets in their own batches', () => {
    const ch4 = model.instances.get('~CH-004')!;
    expect(model.batches[ch4.batch].unexported).toBe(true);
    const ch1 = model.instances.get('Chiller/R_C1')!;
    expect(model.batches[ch1.batch].unexported).toBe(false);
    expect(model.batches[ch1.batch].family).toBe(model.batches[ch4.batch].family);
  });

  it('batches by silhouette so draw calls stay few', () => {
    expect(model.batches.length).toBeLessThanOrEqual(2 * FAMILIES.length);
  });
});

describe('click-through picking', () => {
  const hits = ['~CCU-001', 'Temperature and Humidity/Datahall 1/Sensor 4', 'CRAC/L1_CRAC1'];

  it('picks the nearest asset first', () => {
    expect(nextPick(hits, null)).toBe(hits[0]);
    expect(nextPick(hits, 'Chiller/R_C1')).toBe(hits[0]);
  });

  it('clicking the selected asset again picks the one behind it, then wraps', () => {
    expect(nextPick(hits, hits[0])).toBe(hits[1]);
    expect(nextPick(hits, hits[1])).toBe(hits[2]);
    expect(nextPick(hits, hits[2])).toBe(hits[0]);
  });

  it('picks nothing when nothing was hit', () => {
    expect(nextPick([], 'Chiller/R_C1')).toBeUndefined();
  });
});
