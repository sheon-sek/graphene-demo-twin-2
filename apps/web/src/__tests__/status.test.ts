import { describe, expect, it } from 'vitest';
import { assetStatus, type StatusInput } from '../lib/status';
import type { Quality, SourceClass, Value } from '../lib/types';

/** A point; fault/alarm points are alarm bits unless their name says they count or enumerate. */
function p(name: string, sourceClass: SourceClass, value: Value, quality: Quality = 'good') {
  const alarmBit = sourceClass === 'fault_alarm' && !/ (Count|Code|Status)$/.test(name);
  return { path: `Chiller/R_C1/${name}`, sourceClass, alarmBit, reading: { value, quality } };
}

const running: StatusInput[] = [
  p('On_Off', 'feedback', 1),
  p('General Alarm', 'fault_alarm', false),
  p('Chilled Water Supply Temperature', 'process_value', 6.7),
];

describe('asset state colour', () => {
  it('is normal when running with no alarm bits', () => {
    expect(assetStatus(running)).toBe('normal');
  });

  it('is alarm when a boolean fault or alarm bit is set', () => {
    expect(assetStatus([...running, p('Trip', 'fault_alarm', true)])).toBe('alarm');
  });

  it('is alarm when an integer alarm bit is nonzero', () => {
    expect(assetStatus([...running, p('Trip', 'fault_alarm', 1)])).toBe('alarm');
    expect(assetStatus([...running, p('Low Coolant Level', 'fault_alarm', 1)])).toBe('alarm');
    expect(assetStatus([...running, p('EF', 'fault_alarm', 0)])).toBe('normal');
  });

  it('reads alarm bits from the point metadata, not from the value alone', () => {
    expect(assetStatus([...running, p('Active Count', 'fault_alarm', 3)])).toBe('normal');
    const bit = { ...p('Leak', 'process_value', 1), alarmBit: true };
    expect(assetStatus([...running, bit])).toBe('alarm');
  });

  it('is warning for warning and pre-alarm bits', () => {
    expect(assetStatus([...running, p('Bypass Undervoltage Warning', 'fault_alarm', true)])).toBe(
      'warning',
    );
    expect(assetStatus([...running, p('Genset Prealarm', 'fault_alarm', true)])).toBe('warning');
  });

  it('is warning when a point is of uncertain quality', () => {
    expect(assetStatus([...running, p('Flow', 'process_value', 3, 'uncertain')])).toBe('warning');
  });

  it('ignores alarm summaries that are text or counts', () => {
    expect(assetStatus([...running, p('Alarm Status', 'fault_alarm', 'NO ALARMS')])).toBe('normal');
    expect(assetStatus([...running, p('Unit Safety Fault Code', 'fault_alarm', 0)])).toBe('normal');
  });

  it('is offline when every run feedback reports stopped', () => {
    expect(assetStatus([p('On_Off', 'feedback', 0), p('Fan On_Off', 'feedback', false)])).toBe(
      'offline',
    );
    expect(assetStatus([p('On_Off', 'feedback', 0), p('Fan On_Off', 'feedback', true)])).toBe(
      'normal',
    );
  });

  it('lets an alarm outrank stopped', () => {
    expect(assetStatus([p('On_Off', 'feedback', 0), p('Trip', 'fault_alarm', true)])).toBe(
      'alarm',
    );
  });

  it('is bad when any point is bad quality, whatever the values say', () => {
    expect(assetStatus([...running, p('Trip', 'fault_alarm', true, 'bad')])).toBe('bad');
  });

  it('is unknown until readings arrive', () => {
    expect(assetStatus([])).toBe('unknown');
    expect(
      assetStatus([
        { path: 'x', sourceClass: 'process_value', alarmBit: false, reading: undefined },
      ]),
    ).toBe('unknown');
  });
});
