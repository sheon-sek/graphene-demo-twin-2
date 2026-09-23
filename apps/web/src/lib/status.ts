import type { Reading, SourceClass } from './types';

/**
 * The console's state language. `bad` is drawn grey with hatching; `unknown` means no reading
 * has arrived yet.
 */
export type Status = 'normal' | 'warning' | 'alarm' | 'offline' | 'bad' | 'unknown';

export interface StatusInput {
  path: string;
  sourceClass: SourceClass;
  reading: Reading | undefined;
}

const WARNING = /warning|prealarm/i;
/** Feedback points that say whether the equipment is running. */
const RUN_FEEDBACK = /^(On_?Off|Fan On_Off|Compressor On_Off Status|EC Fan Run Status|Unit Running Status)$/;

const nameOf = (path: string) => path.slice(path.lastIndexOf('/') + 1);

/**
 * An asset's state from its points, first rule that holds:
 * any bad quality → bad; a set boolean fault/alarm bit → alarm (warning when its name says
 * warning or pre-alarm); any uncertain quality → warning; every run feedback stopped → offline.
 * Text and count summaries of alarms are not read: they duplicate the bits.
 */
export function assetStatus(points: Iterable<StatusInput>): Status {
  let seen = false;
  let alarm = false;
  let warning = false;
  let runFeedback = 0;
  let running = 0;
  for (const { path, sourceClass, reading } of points) {
    if (!reading) continue;
    seen = true;
    if (reading.quality === 'bad') return 'bad';
    if (reading.quality === 'uncertain') warning = true;
    const name = nameOf(path);
    if (sourceClass === 'fault_alarm' && reading.value === true) {
      if (WARNING.test(name)) warning = true;
      else alarm = true;
    } else if (sourceClass === 'feedback' && RUN_FEEDBACK.test(name)) {
      runFeedback++;
      if (reading.value === true || (typeof reading.value === 'number' && reading.value !== 0)) {
        running++;
      }
    }
  }
  if (!seen) return 'unknown';
  if (alarm) return 'alarm';
  if (warning) return 'warning';
  if (runFeedback > 0 && running === 0) return 'offline';
  return 'normal';
}
