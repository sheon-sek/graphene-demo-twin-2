import { useMemo, useState } from 'react';
import { locate } from '../actions';
import { search } from '../lib/search';
import type { World } from '../lib/world';

/** Locate an asset by export path: Enter or a click selects it and flies to it. */
export function SearchBox({ world }: { world: World }) {
  const [query, setQuery] = useState('');
  const hits = useMemo(() => search(world.search, query, 12), [world, query]);
  const choose = (node: string) => {
    locate(world, node);
    setQuery('');
  };
  return (
    <div className="search">
      <input
        type="search"
        placeholder="Locate by export path…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && hits.length) choose(hits[0].selects);
          if (e.key === 'Escape') setQuery('');
        }}
      />
      {hits.length > 0 && (
        <ul className="search-hits" role="listbox">
          {hits.map((hit) => (
            <li key={hit.path} role="option" aria-selected={false} onClick={() => choose(hit.selects)}>
              {hit.path}
              {hit.selects !== hit.path && <span className="hint"> → {hit.selects}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
