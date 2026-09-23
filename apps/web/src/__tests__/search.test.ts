import { describe, expect, it } from 'vitest';
import { buildSearchIndex, search } from '../lib/search';
import { plantDesign } from './fixtures';

const design = plantDesign();
const assets = design.assets.filter((a) => !a.unexported).map((a) => a.path);
const index = buildSearchIndex(assets, design.unexported);

describe('search by export path', () => {
  it('finds an asset by its full export path first', () => {
    expect(search(index, 'Chiller/R_C1')[0]).toEqual({ path: 'Chiller/R_C1', selects: 'Chiller/R_C1' });
  });

  it('is case-insensitive and ranks the last segment above deeper substrings', () => {
    const hits = search(index, 'r_c1');
    expect(hits[0].selects).toBe('Chiller/R_C1');
  });

  it('locates Unexported Assets by their Plant View export paths', () => {
    const hits = search(index, 'Chiller_System/Chillers/CH-004');
    expect(hits[0]).toEqual({ path: 'Chiller_System/Chillers/CH-004', selects: '~CH-004' });
    expect(search(index, 'CH-004').every((h) => h.selects === '~CH-004')).toBe(true);
  });

  it('caps the result list and returns nothing for a blank query', () => {
    expect(search(index, 'Environment', 10)).toHaveLength(10);
    expect(search(index, '  ')).toEqual([]);
  });
});
