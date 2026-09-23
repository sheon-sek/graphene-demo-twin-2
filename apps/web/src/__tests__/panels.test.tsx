import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { AssetTree } from '../components/AssetTree';
import { Inspector } from '../components/Inspector';
import { SearchBox } from '../components/SearchBox';
import { LiveStore } from '../lib/live';
import type { PointInfo } from '../lib/types';
import { buildWorld } from '../lib/world';
import { initialConsoleState, useConsole } from '../store';
import { plantDesign } from './fixtures';

const design = plantDesign();
const assetPaths = design.assets.filter((a) => !a.unexported).map((a) => a.path);
const points: PointInfo[] = [
  ...assetPaths.map(
    (p): PointInfo => ({ path: `${p}/Status`, sourceClass: 'equipment_state', alarmBit: false }),
  ),
  { path: 'Chiller/R_C1/On_Off', sourceClass: 'feedback', alarmBit: false },
  { path: 'Chiller/R_C1/General Alarm', sourceClass: 'fault_alarm', alarmBit: true },
  {
    path: 'Chiller/R_C1/Chilled Water Supply Temperature',
    sourceClass: 'process_value',
    alarmBit: false,
  },
  {
    path: 'Chiller_System/Chillers/CH-004/Supply Temperature',
    sourceClass: 'process_value',
    alarmBit: false,
  },
];
const world = buildWorld(design, assetPaths, points);

function frame(seq: number, supply: number) {
  return {
    seq,
    epoch: 0,
    time: 1_790_000_000 + seq,
    timestamp: `2026-09-21T14:13:${String(20 + seq).padStart(2, '0')}Z`,
    events: 0,
    points: {
      'Chiller/R_C1/On_Off': { value: 1, quality: 'good' as const },
      'Chiller/R_C1/General Alarm': { value: false, quality: 'good' as const },
      'Chiller/R_C1/Chilled Water Supply Temperature': { value: supply, quality: 'good' as const },
      'Chiller_System/Chillers/CH-004/Supply Temperature': { value: 7, quality: 'bad' as const },
    },
  };
}

beforeEach(() => useConsole.setState({ ...initialConsoleState, expanded: new Set() }));
afterEach(cleanup);

describe('Asset Model tree', () => {
  it('selects the asset clicked in the tree', () => {
    render(<AssetTree world={world} />);
    fireEvent.click(screen.getByRole('treeitem', { name: 'Chiller' }));
    fireEvent.click(screen.getByRole('treeitem', { name: 'R_C1' }));
    expect(useConsole.getState().selected).toBe('Chiller/R_C1');
  });

  it('reveals and marks the asset selected elsewhere (the 3D scene)', () => {
    render(<AssetTree world={world} />);
    expect(screen.queryByRole('treeitem', { name: 'R_C2' })).toBeNull();
    act(() => useConsole.getState().select('Chiller/R_C2'));
    const row = screen.getByRole('treeitem', { name: 'R_C2' });
    expect(row.getAttribute('aria-selected')).toBe('true');
  });

  it('selects an Unexported Asset through the Plant View folder that observes it', () => {
    render(<AssetTree world={world} />);
    act(() => useConsole.getState().select('~CH-004'));
    const row = screen.getByRole('treeitem', { name: 'CH-004' });
    expect(row.getAttribute('aria-selected')).toBe('true');
    act(() => useConsole.getState().select(null));
    fireEvent.click(row);
    expect(useConsole.getState().selected).toBe('~CH-004');
  });
});

describe('inspector, Points tab', () => {
  it('shows each point with its live value, quality, timestamp and sparkline', () => {
    const live = new LiveStore(10);
    live.apply('snapshot', frame(1, 6.5));
    render(<Inspector world={world} live={live} />);
    act(() => useConsole.getState().select('Chiller/R_C1'));
    act(() => live.apply('delta', frame(2, 6.9)));

    const row = screen.getByRole('row', { name: /Chilled Water Supply Temperature/ });
    expect(within(row).getByText('6.9')).toBeTruthy();
    expect(within(row).getByText('good')).toBeTruthy();
    expect(within(row).getByText('14:13:22')).toBeTruthy();
    expect(row.querySelector('svg path')?.getAttribute('d')).toMatch(/^M.*L/);
    expect(screen.getByRole('row', { name: /General Alarm/ }).textContent).toContain('false');
  });

  it('shows the Plant View points of an Unexported Asset, bad quality marked', () => {
    const live = new LiveStore(10);
    live.apply('snapshot', frame(1, 6.5));
    render(<Inspector world={world} live={live} />);
    act(() => useConsole.getState().select('~CH-004'));
    const row = screen.getByRole('row', { name: /Supply Temperature/ });
    expect(within(row).getByText('bad').className).toContain('bad');
    expect(screen.getByText(/Unexported Asset/)).toBeTruthy();
  });

  it('lists upstream and downstream neighbours, and selects one on click', () => {
    render(<Inspector world={world} live={new LiveStore(10)} />);
    act(() => useConsole.getState().select('Chiller/R_C1'));
    fireEvent.click(screen.getByRole('tab', { name: 'Connections' }));
    fireEvent.click(screen.getByRole('button', { name: 'Chiller/R_CP1' }));
    expect(useConsole.getState().selected).toBe('Chiller/R_CP1');
  });
});

describe('search', () => {
  it('locates an asset by export path: selects it and flies to it on its floor', () => {
    render(<SearchBox world={world} />);
    const box = screen.getByRole('searchbox');
    fireEvent.change(box, { target: { value: 'Chiller/R_C3' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    const state = useConsole.getState();
    expect(state.selected).toBe('Chiller/R_C3');
    expect(state.view).toMatchObject({ kind: 'asset', path: 'Chiller/R_C3' });
    expect(state.cutaway).toBe(3);
  });
});
