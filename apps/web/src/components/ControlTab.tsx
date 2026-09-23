import { useEffect, useState, useSyncExternalStore } from 'react';
import { api } from '../lib/api';
import { formatValue } from '../lib/format';
import type { LiveStore } from '../lib/live';
import type { CommandInfo, Value } from '../lib/types';

/**
 * Operator Commands on the selected asset (hand/auto, start/stop, setpoints), each written
 * to the Event Log. Current values are refetched whenever the Event Log grows or resets.
 */
export function ControlTab({ live, node }: { live: LiveStore; node: string }) {
  useSyncExternalStore(
    (l) => live.subscribe(l),
    () => live.version,
  );
  const [commands, setCommands] = useState<CommandInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { eventCount, epoch } = live;

  useEffect(() => {
    let cancelled = false;
    api
      .commands(node)
      .then((body) => !cancelled && setCommands(body.commands))
      .catch((e: unknown) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, [node, eventCount, epoch]);

  if (error) return <p className="error pad">{error}</p>;
  if (!commands) return <p className="hint pad">Loading…</p>;
  if (!commands.length) return <p className="hint pad">This asset takes no Operator Commands.</p>;

  const send = (command: string, value: Value) => {
    setError(null);
    api.sendCommand(node, command, value).catch((e: unknown) => setError(String(e)));
  };
  const auto = commands.find((c) => c.name === 'mode')?.value === 'auto';

  return (
    <div className="control-tab">
      {commands.map((c) => (
        <fieldset key={c.name} role="group" aria-label={c.label} className="command">
          <legend>{c.label}</legend>
          {c.kind === 'choice' &&
            c.choices.map((choice) => (
              <button
                key={choice}
                aria-pressed={c.value === choice}
                className={c.value === choice ? 'on' : ''}
                onClick={() => send(c.name, choice)}
              >
                {choice}
              </button>
            ))}
          {c.kind === 'switch' && (
            <>
              {(['Start', 'Stop'] as const).map((label) => (
                <button
                  key={label}
                  aria-pressed={c.value === (label === 'Start')}
                  className={c.value === (label === 'Start') ? 'on' : ''}
                  disabled={auto}
                  onClick={() => send(c.name, label === 'Start')}
                >
                  {label}
                </button>
              ))}
              {auto && <span className="hint">in auto, the Controller runs it</span>}
            </>
          )}
          {c.kind === 'number' && <NumberCommand command={c} onSend={send} />}
        </fieldset>
      ))}
    </div>
  );
}

function NumberCommand({
  command,
  onSend,
}: {
  command: CommandInfo;
  onSend: (command: string, value: Value) => void;
}) {
  const [draft, setDraft] = useState(String(command.value ?? ''));
  const value = Number(draft);
  const valid =
    draft !== '' &&
    Number.isFinite(value) &&
    (command.minimum === null || value >= command.minimum) &&
    (command.maximum === null || value <= command.maximum);
  return (
    <>
      <span className="current">
        now {formatValue(command.value)} {command.unit}
      </span>
      <input
        type="number"
        step={0.5}
        min={command.minimum ?? undefined}
        max={command.maximum ?? undefined}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
      />
      <button disabled={!valid} onClick={() => onSend(command.name, value)}>
        Set
      </button>
    </>
  );
}
