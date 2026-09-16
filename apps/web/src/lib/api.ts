export type Status={mode:string;state:string;simulationTimestamp:string;timeScale:number;activeInjectedFaults:number;activeOverrides:number};
export type Asset={assetId:string;name:string;typeId:string;exportPath:string;system:string;zoneId:string;scope:string};
export type FaultActivation={injectionId:string;recipeId:string;targetAsset:string;severity:number;simulationTimestamp:string;source:string;rampSeconds?:number;durationSeconds?:number|null;parameters?:Record<string,unknown>|null};
export type TopologyRelation={kind:string;from:string;to:string};
export type Topology={site:{siteId:string};zones:Array<{id:string;label:string}>;assets:Asset[];relations:TopologyRelation[]};
export type Snapshot={status:Status;site:Record<string,number>;faults:FaultActivation[];points?:Record<string,any>};
const j=async<T>(url:string,init?:RequestInit):Promise<T>=>{const r=await fetch(url,init);if(!r.ok)throw new Error(await r.text());return r.json()};
export const api={status:()=>j<Status>('/api/status'),snapshot:(points=false)=>j<Snapshot>(`/api/snapshot?points=${points}`),assets:()=>j<{assets:Asset[]}>('/api/assets'),topology:()=>j<Topology>('/api/topology'),catalog:()=>j<any>('/api/faults/catalog'),faults:()=>j<any>('/api/faults/active'),events:()=>j<any>('/api/events'),logs:()=>j<any>('/api/logs'),coverage:()=>j<any>('/api/schema/coverage'),command:(path:string,extra={})=>j<any>(path,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({commandId:crypto.randomUUID(),confirm:true,...extra})})};
