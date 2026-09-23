import { describe, expect, it } from 'vitest';
import { encodePath } from '../lib/api';

describe('export paths in URLs', () => {
  it('percent-encodes each segment and keeps the slashes', () => {
    expect(encodePath('Chiller System Control/Chillers/CH-004')).toBe(
      'Chiller%20System%20Control/Chillers/CH-004',
    );
    expect(encodePath('Meter/GEM630:a#1?')).toBe('Meter/GEM630%3Aa%231%3F');
  });
});
