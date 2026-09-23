import type { Reading, SourceClass } from './types';

/**
 * The console's state language. `bad` is drawn grey with hatching; `unknown` means no reading
 * has arrived yet.
 */
export type Status = 'normal' | 'warning' | 'alarm' | 'offline' | 'bad' | 'unknown';

export interface StatusInput {
  path: string;
  sourceClass: SourceClass;
  /** From the point metadata: a fault or alarm bit, active when true or nonzero. */
  alarmBit: boolean;
  reading: Reading | undefined;
}

const WARNING = /warning|prealarm/i;
/** Feedback points that say whether the equipment is running. */
const RUN_FEEDBACK = /^(On_?Off|Fan On_Off|Compressor On_Off Status|EC Fan Run Status|Unit Running Status)$/;

const nameOf = (path: string) => path.slice(path.lastIndexOf('/') + 1);
const isSet = (value: Reading['value']) =>
  value === true || (typeof value === 'number' && value !== 0);

/**
 * An asset's state from its points, first rule that holds:
 * any bad quality → bad; a set alarm bit (true or nonzero) → alarm, or warning when its name
 * says warning or pre-alarm; any uncertain quality → warning; every run feedback stopped →
 * offline. Alarm counts, codes and texts are not alarm bits: they duplicate the bits.
 */
export function assetStatus(points: Iterable<StatusInput>): Status {
  let seen = false;
  let alarm = false;
  let warning = false;
  let runFeedback = 0;
  let running = 0;
  for (const { path, sourceClass, alarmBit, reading } of points) {
    if (!reading) continue;
    seen = true;
    if (reading.quality === 'bad') return 'bad';
    if (reading.quality === 'uncertain') warning = true;
    const name = nameOf(path);
    if (alarmBit && isSet(reading.value)) {
      if (WARNING.test(name)) warning = true;
      else alarm = true;
    } else if (sourceClass === 'feedback' && RUN_FEEDBACK.test(name)) {
      runFeedback++;
      if (isSet(reading.value)) running++;
    }
  }
  if (!seen) return 'unknown';
  if (alarm) return 'alarm';
  if (warning) return 'warning';
  if (runFeedback > 0 && running === 0) return 'offline';
  return 'normal';
}
