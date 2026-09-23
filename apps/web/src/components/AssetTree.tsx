import { useEffect, useMemo, useRef } from 'react';
import { locate } from '../actions';
import { ancestorsOf, flattenVisible } from '../lib/tree';
import type { World } from '../lib/world';
import { useConsole } from '../store';

/** The Asset Model tree in exact export hierarchy; selection is shared with the 3D scene. */
export function AssetTree({ world }: { world: World }) {
  const { tree } = world;
  const selected = useConsole((s) => s.selected);
  const expanded = useConsole((s) => s.expanded);
  const toggleExpanded = useConsole((s) => s.toggleExpanded);
  const expand = useConsole((s) => s.expand);
  const selectedRow = selected === null ? undefined : tree.nodeFor.get(selected);
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (selectedRow) expand(ancestorsOf(tree, selectedRow));
  }, [selectedRow, tree, expand]);

  useEffect(() => {
    if (!selectedRow) return;
    const rows = list.current?.children ?? [];
    const row = [...rows].find((r) => (r as HTMLElement).dataset.path === selectedRow);
    row?.scrollIntoView?.({ block: 'nearest' });
  }, [selectedRow, expanded]);

  const rows = useMemo(() => flattenVisible(tree, expanded), [tree, expanded]);

  return (
    <div className="tree" role="tree" aria-label="Asset Model" ref={list}>
      {rows.map(({ path, depth }) => {
        const node = tree.byPath.get(path)!;
        const isOpen = expanded.has(path);
        const hasChildren = node.children.length > 0;
        const isSelected = path === selectedRow;
        return (
          <div
            key={path}
            role="treeitem"
            aria-label={node.name}
            aria-level={depth + 1}
            aria-selected={isSelected}
            aria-expanded={hasChildren ? isOpen : undefined}
            data-path={path}
            className={`tree-row ${node.kind}${isSelected ? ' selected' : ''}${
              node.selects?.startsWith('~') ? ' unexported' : ''
            }`}
            style={{ paddingLeft: 8 + depth * 14 }}
            title={path}
            onClick={() => {
              if (hasChildren) toggleExpanded(path);
              if (node.selects) locate(world, node.selects);
            }}
          >
            <span className="twisty">{hasChildren ? (isOpen ? '▾' : '▸') : ''}</span>
            <span className="tree-name">{node.name}</span>
          </div>
        );
      })}
    </div>
  );
}
