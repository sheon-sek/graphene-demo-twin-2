import type { UnexportedAsset } from './types';

export interface SearchHit {
  /** The export path that matched. */
  path: string;
  /** The Plant Design node (or Support Asset) selecting the hit selects. */
  selects: string;
}

interface Entry extends SearchHit {
  lower: string;
  lastSegment: string;
}

export type SearchIndex = Entry[];

/** Every Asset by export path, and every Unexported Asset by its Plant View folders. */
export function buildSearchIndex(
  assetPaths: Iterable<string>,
  unexported: UnexportedAsset[],
): SearchIndex {
  const entry = (path: string, selects: string): Entry => ({
    path,
    selects,
    lower: path.toLowerCase(),
    lastSegment: path.slice(path.lastIndexOf('/') + 1).toLowerCase(),
  });
  return [
    ...[...assetPaths].map((p) => entry(p, p)),
    ...unexported.flatMap((u) => u.observedBy.map((f) => entry(f, u.id))),
  ];
}

/**
 * Case-insensitive match on export paths. Ranked: whole path, last segment, path prefix,
 * a segment starting with the query, then any substring; shorter paths first within a rank.
 */
export function search(index: SearchIndex, query: string, limit = 20): SearchHit[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const ranked: [number, Entry][] = [];
  for (const e of index) {
    const at = e.lower.indexOf(q);
    if (at < 0) continue;
    const rank =
      e.lower === q
        ? 0
        : e.lastSegment === q
          ? 1
          : at === 0
            ? 2
            : e.lower[at - 1] === '/'
              ? 3
              : 4;
    ranked.push([rank, e]);
  }
  ranked.sort(
    ([ra, a], [rb, b]) => ra - rb || a.path.length - b.path.length || a.path.localeCompare(b.path),
  );
  return ranked.slice(0, limit).map(([, e]) => ({ path: e.path, selects: e.selects }));
}
