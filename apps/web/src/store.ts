import { create } from 'zustand';
import type { View } from './lib/camera';
import { LAYERS, type Layer } from './lib/routing';

export interface ConsoleState {
  /** Selected Plant Design node (asset path or `~id`) or Support Asset path. */
  selected: string | null;
  hovered: string | null;
  /** The framed view; `nonce` re-flies to the same view when asked again. */
  view: View & { nonce: number };
  /** Floors above this index are cut away so the framed floor is visible; null shows all. */
  cutaway: number | null;
  /** Requested floor separation, 0 (collapsed) to 1 (exploded). */
  explodeTarget: number;
  /** Animated floor separation actually drawn this frame. */
  explode: number;
  layers: Record<Layer, boolean>;
  expanded: ReadonlySet<string>;

  select(node: string | null): void;
  hover(node: string | null): void;
  goTo(view: View, cutaway: number | null): void;
  setExplodeTarget(value: number): void;
  setExplode(value: number): void;
  toggleLayer(layer: Layer): void;
  setCutaway(floor: number | null): void;
  toggleExpanded(path: string): void;
  expand(paths: string[]): void;
}

export const initialConsoleState = {
  selected: null,
  hovered: null,
  view: { kind: 'site' as const, nonce: 0 },
  cutaway: null,
  explodeTarget: 1,
  explode: 1,
  layers: Object.fromEntries(LAYERS.map((l) => [l, true])) as Record<Layer, boolean>,
  expanded: new Set<string>(),
};

export const useConsole = create<ConsoleState>((set) => ({
  ...initialConsoleState,
  select: (selected) => set({ selected }),
  hover: (hovered) => set({ hovered }),
  goTo: (view, cutaway) => set((s) => ({ view: { ...view, nonce: s.view.nonce + 1 }, cutaway })),
  setExplodeTarget: (explodeTarget) => set({ explodeTarget }),
  setExplode: (explode) => set({ explode }),
  toggleLayer: (layer) => set((s) => ({ layers: { ...s.layers, [layer]: !s.layers[layer] } })),
  setCutaway: (cutaway) => set({ cutaway }),
  toggleExpanded: (path) =>
    set((s) => {
      const expanded = new Set(s.expanded);
      if (!expanded.delete(path)) expanded.add(path);
      return { expanded };
    }),
  expand: (paths) =>
    set((s) =>
      paths.every((p) => s.expanded.has(p)) ? s : { expanded: new Set([...s.expanded, ...paths]) },
    ),
}));
