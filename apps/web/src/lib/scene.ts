import type {Asset, Topology, TopologyRelation} from './api';

export type Vec3 = [number, number, number];
export type SceneAreaId = 'cooling'|'hall'|'electrical'|'network'|'utility'|'liquid'|'facility';
export type SceneLayer = 'cooling'|'airside'|'power'|'network'|'water'|'liquid'|'safety'|'environment';
export type AssetModelFamily = 'chiller'|'pump'|'valve'|'tower'|'air-handler'|'ups'|'genset'|'network-switch'|'server'|'panel'|'tank'|'cdu'|'sensor'|'generic';

export type SceneArea = {
  id: SceneAreaId;
  label: string;
  center: [number, number];
  size: [number, number];
  accent: string;
};

export type ScenePlacement = {
  asset: Asset;
  areaId: SceneAreaId;
  layer: SceneLayer;
  family: AssetModelFamily;
  position: Vec3;
};

export type SceneLink = {
  id: string;
  kind: 'serves'|'feeds'|'networkParent';
  from: string;
  to: string;
  points: [Vec3, Vec3];
};

export const SCENE_AREAS: Record<SceneAreaId, SceneArea> = {
  cooling: {id:'cooling', label:'Cooling Plant', center:[-11,-6], size:[18,11], accent:'#4e94a8'},
  hall: {id:'hall', label:'Data Hall Airside', center:[7,-6], size:[16,11], accent:'#4b928a'},
  electrical: {id:'electrical', label:'Electrical', center:[-11,7], size:[20,11], accent:'#a4884f'},
  network: {id:'network', label:'Network', center:[4,7], size:[9,11], accent:'#756f9d'},
  utility: {id:'utility', label:'Water / Utility', center:[12.5,7], size:[8,11], accent:'#4e759c'},
  liquid: {id:'liquid', label:'Liquid Cooling', center:[18.5,-6], size:[6,11], accent:'#4b8f96'},
  facility: {id:'facility', label:'Facility / Safety', center:[19.5,7], size:[5,11], accent:'#7f7468'},
};

const ABSTRACT_TYPES = new Set(['dashboard','di event test']);
const COOLING_PLANT_TYPES = new Set(['chiller','chiller pump','chiller valve','cooling tower','makeup water pump','buffer tank']);
const AIRSIDE_TYPES = new Set(['crac','pahu','fwu','fcu']);
const POWER_TYPES = new Set(['ups','genset','ips','rcms','bcpm','gpm96','gpqm96','gpqm144','gpqm144 pro','gem130','gem230','gem630','gem630-ct-l','gdc230','e820']);
const NETWORK_TYPES = new Set(['network switch','network device']);
const WATER_TYPES = new Set(['cw booster pump','cw ground tank','cw ground valve','cw roof tank','cw roof valve','cw transfer pump','ac makeup pump','diesel']);
const ENVIRONMENT_TYPES = new Set(['environment monitoring','temperature and humidity']);
const SAFETY_TYPES = new Set(['water leak cable sensor']);

const norm = (value?: string) => (value || '').trim().toLowerCase();

export function sceneLayerForAsset(asset: Asset): SceneLayer | null {
  if (asset.scope && asset.scope !== 'PRODUCTION') return null;
  const type = norm(asset.typeId);
  const system = norm(asset.system);
  if (!type || ABSTRACT_TYPES.has(type)) return null;
  // Port telemetry remains available through topology/inspection, but 192 switch ports are not
  // separate facility-scale 3D equipment. Rendering them individually creates visual noise and
  // multiplies draw calls without improving the digital-twin overview.
  if (type === 'network switch port') return null;
  if (type === 'cdu' || system === 'tiw' || system.includes('liquid')) return 'liquid';
  if (ENVIRONMENT_TYPES.has(type) || system.includes('environment monitoring') || system.includes('temperature and humidity')) return 'environment';
  if (SAFETY_TYPES.has(type) || system.includes('water leak') || system.includes('fire protection')) return 'safety';
  if (NETWORK_TYPES.has(type) || system.includes('network')) return 'network';
  if (AIRSIDE_TYPES.has(type)) return 'airside';
  if (COOLING_PLANT_TYPES.has(type) || system.includes('chiller') || system.includes('cooling tower') || system.includes('buffer tank')) return 'cooling';
  if (POWER_TYPES.has(type) || ['meter','bcpm','breaker','ups','ips','rcms','genset'].some(x=>system === x || system.startsWith(`${x}/`))) return 'power';
  if (WATER_TYPES.has(type) || system.includes('cold water') || system.includes('ac makeup') || system.includes('diesel')) return 'water';
  if (system.includes('lift') || system.includes('level_monitoring')) return 'safety';
  return null;
}

export function areaForAsset(asset: Asset): SceneAreaId {
  const layer = sceneLayerForAsset(asset);
  if (layer === 'cooling') return 'cooling';
  if (layer === 'airside' || layer === 'environment') return 'hall';
  if (layer === 'power') return 'electrical';
  if (layer === 'network') return 'network';
  if (layer === 'water') return 'utility';
  if (layer === 'liquid') return 'liquid';
  return 'facility';
}

export function modelFamilyForAsset(asset: Asset): AssetModelFamily {
  const type = norm(asset.typeId);
  const system = norm(asset.system);
  if (type === 'chiller') return 'chiller';
  if (type.includes('pump')) return 'pump';
  if (type.includes('valve')) return 'valve';
  if (type === 'cooling tower') return 'tower';
  if (AIRSIDE_TYPES.has(type)) return 'air-handler';
  if (type === 'ups') return 'ups';
  if (type === 'genset' || type === 'diesel') return 'genset';
  if (type === 'network switch') return 'network-switch';
  if (type === 'network device') return 'server';
  if (type === 'buffer tank' || type.includes('tank')) return 'tank';
  if (type === 'cdu') return 'cdu';
  if (ENVIRONMENT_TYPES.has(type) || SAFETY_TYPES.has(type)) return 'sensor';
  if (sceneLayerForAsset(asset) === 'power' || system === 'meter' || system === 'bcpm') return 'panel';
  return 'generic';
}

function placeGroup(items: Asset[], area: SceneArea, dense = false): ScenePlacement[] {
  if (!items.length) return [];
  const width = Math.max(2, area.size[0] - 1.5);
  const depth = Math.max(2, area.size[1] - 1.5);
  const aspect = width / depth;
  const cols = Math.max(1, Math.ceil(Math.sqrt(items.length * aspect)));
  const rows = Math.max(1, Math.ceil(items.length / cols));
  const maxStep = dense ? 0.72 : 1.25;
  const stepX = cols <= 1 ? 0 : Math.min(maxStep, width / Math.max(1, cols - 1));
  const stepZ = rows <= 1 ? 0 : Math.min(maxStep, depth / Math.max(1, rows - 1));
  const spanX = stepX * Math.max(0, cols - 1);
  const spanZ = stepZ * Math.max(0, rows - 1);
  const startX = area.center[0] - spanX / 2;
  const startZ = area.center[1] - spanZ / 2;
  return items.map((asset,index)=>{
    const layer = sceneLayerForAsset(asset)!;
    const column = index % cols;
    const row = Math.floor(index / cols);
    return {
      asset,
      areaId: area.id,
      layer,
      family: modelFamilyForAsset(asset),
      position: [startX + column * stepX, dense ? 0.1 : 0, startZ + row * stepZ] as Vec3,
    };
  });
}

export function buildSceneLayout(assets: Asset[]): ScenePlacement[] {
  const physical = assets.filter(a=>sceneLayerForAsset(a) !== null);
  const placements: ScenePlacement[] = [];
  for (const area of Object.values(SCENE_AREAS)) {
    const inArea = physical.filter(a=>areaForAsset(a) === area.id).sort((a,b)=>{
      const la = sceneLayerForAsset(a)!;
      const lb = sceneLayerForAsset(b)!;
      return la.localeCompare(lb) || a.typeId.localeCompare(b.typeId) || a.name.localeCompare(b.name) || a.exportPath.localeCompare(b.exportPath);
    });
    const dense = inArea.filter(a=>['environment','safety'].includes(sceneLayerForAsset(a)!));
    const primary = inArea.filter(a=>!['environment','safety'].includes(sceneLayerForAsset(a)!));
    placements.push(...placeGroup(primary, area, false), ...placeGroup(dense, area, true));
  }
  return placements;
}

export function buildSceneLinks(topology: Topology | null, placements: ScenePlacement[], selectedAssetId?: string | null): SceneLink[] {
  if (!topology) return [];
  const byId = new Map(placements.map(p=>[p.asset.assetId,p]));
  const allowed = new Set(['serves','feeds','networkParent']);
  const links: SceneLink[] = [];
  const seen = new Set<string>();
  for (const relation of topology.relations || []) {
    if (!allowed.has(relation.kind)) continue;
    if (selectedAssetId && relation.from !== selectedAssetId && relation.to !== selectedAssetId) continue;
    if (!selectedAssetId && relation.kind === 'feeds') continue;
    const from = byId.get(relation.from);
    const to = byId.get(relation.to);
    if (!from || !to) continue;
    const key = `${relation.kind}:${relation.from}:${relation.to}`;
    if (seen.has(key)) continue;
    seen.add(key);
    links.push({
      id:key,
      kind:relation.kind as SceneLink['kind'],
      from:relation.from,
      to:relation.to,
      points:[[from.position[0],0.16,from.position[2]],[to.position[0],0.16,to.position[2]]],
    });
    if (links.length >= 120) break;
  }
  return links;
}

export function relatedRelations(topology: Topology | null, assetId: string): TopologyRelation[] {
  if (!topology) return [];
  return topology.relations.filter(r=>r.from === assetId || r.to === assetId);
}
