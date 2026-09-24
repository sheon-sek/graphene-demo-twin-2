import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { api } from '../lib/api';
import { shortName } from '../lib/format';
import type { LiveStore } from '../lib/live';
import type { EventLogDoc, PlayResult } from '../lib/types';

type Pending = { doc: EventLogDoc; source: 'golden' | 'import' };

/** Load and play the Golden Demo against the Live World, and export or import Event Logs.
 * Playing resets the Live World first, so every story starts from steady state. */
export function StoryControls({
  live,
  onError,
}: {
  live: LiveStore;
  onError: (e: unknown) => void;
}) {
  useSyncExternalStore(
    (l) => live.subscribe(l),
    () => live.version,
  );
  const [pending, setPending] = useState<Pending | null>(null);
  const [playing, setPlaying] = useState<{
    doc: EventLogDoc;
    result: PlayResult;
    epoch: number;
  } | null>(null);
  const file = useRef<HTMLInputElement>(null);

  // Playing makes one Reset (the next epoch); any later Reset ends the story being played.
  useEffect(() => {
    if (playing && live.epoch > playing.epoch + 1) setPlaying(null);
  }, [live.epoch, playing]);

  const load = () =>
    api
      .goldenDemo()
      .then((doc) => setPending({ doc, source: 'golden' }))
      .catch(onError);

  const play = ({ doc, source }: Pending) => {
    setPending(null);
    const before = live.epoch;
    const run = source === 'golden' ? api.playGoldenDemo() : api.importEvents(doc);
    run.then((result) => setPlaying({ doc, result, epoch: before })).catch(onError);
  };

  const exportLog = () =>
    api
      .exportEvents()
      .then((doc) => {
        const blob = new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `event-log-${live.time}.json`;
        a.click();
        URL.revokeObjectURL(url);
      })
      .catch(onError);

  const importLog = (f: File) =>
    f
      .text()
      .then((text) => setPending({ doc: JSON.parse(text) as EventLogDoc, source: 'import' }))
      .catch(onError);

  return (
    <section className="panel story" aria-label="Golden Demo">
      <h3>Golden Demo &amp; Event Logs</h3>
      {pending ? (
        <div className="confirming" role="alertdialog" aria-label="Confirm play">
          <p>
            <strong>{pending.doc.title || 'Imported Event Log'}</strong>
          </p>
          {pending.doc.description && <p className="hint">{pending.doc.description}</p>}
          <p className="warn">
            Playing resets the Live World (discarding its Event Log and What-if Forks), then logs{' '}
            {pending.doc.events.length} actions over {minutes(pending.doc)} min from now.
          </p>
          <button className="danger" onClick={() => play(pending)}>
            Reset and play
          </button>
          <button onClick={() => setPending(null)}>Cancel</button>
        </div>
      ) : (
        <div className="story-actions">
          <button onClick={load}>Play Golden Demo…</button>
          <button onClick={exportLog}>Export Event Log</button>
          <button onClick={() => file.current?.click()}>Import Event Log…</button>
          <input
            ref={file}
            type="file"
            accept="application/json,.json"
            hidden
            aria-label="Event Log file"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) importLog(f);
              e.target.value = '';
            }}
          />
        </div>
      )}
      {playing && <Story doc={playing.doc} result={playing.result} now={live.time} />}
    </section>
  );
}

function Story({ doc, result, now }: { doc: EventLogDoc; result: PlayResult; now: number }) {
  const elapsed = Math.max(0, now - result.start);
  return (
    <ol className="story-steps" aria-label="Story">
      {doc.events.map((e, i) => {
        const done = e.offset <= elapsed;
        return (
          <li key={i} className={done ? 'done' : 'pending'} title={e.note ?? ''}>
            <span className="at">+{formatOffset(e.offset)}</span>
            <span className="target">
              {String(e.params.fault ?? e.params.command ?? e.kind)} · {shortName(e.target)}
            </span>
            {e.note && <span className="note">{e.note}</span>}
          </li>
        );
      })}
    </ol>
  );
}

function minutes(doc: EventLogDoc): number {
  return Math.ceil(Math.max(0, ...doc.events.map((e) => e.offset)) / 60);
}

export function formatOffset(s: number): string {
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, '0')}`;
}
