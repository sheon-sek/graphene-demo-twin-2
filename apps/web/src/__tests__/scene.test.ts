import {describe,expect,it} from 'vitest';
import type {Asset,Topology} from '../lib/api';
import {areaForAsset,buildSceneLayout,buildSceneLinks,modelFamilyForAsset,sceneLayerForAsset} from '../lib/scene';

const asset=(overrides:Partial<Asset>):Asset=>({assetId:'asset.test',name:'Test',typeId:'CRAC',exportPath:'CRAC/Test',system:'CRAC',zoneId:'CRAC',scope:'PRODUCTION',...overrides});

describe('3D scene mapping',()=>{
  it('maps physical asset families to semantic areas and layers',()=>{
    expect(areaForAsset(asset({typeId:'Chiller',system:'Chiller'}))).toBe('cooling');
    expect(areaForAsset(asset({typeId:'UPS',system:'UPS'}))).toBe('electrical');
    expect(areaForAsset(asset({typeId:'Network Switch',system:'Network Switches'}))).toBe('network');
    expect(sceneLayerForAsset(asset({typeId:'Temperature and Humidity',system:'Temperature and Humidity'}))).toBe('environment');
    expect(sceneLayerForAsset(asset({typeId:'Network Switch Port',system:'Network Switches'}))).toBeNull();
    expect(modelFamilyForAsset(asset({typeId:'Cooling Tower'}))).toBe('tower');
  });

  it('creates deterministic, non-index-global placement',()=>{
    const assets=[
      asset({assetId:'c1',name:'CH-1',typeId:'Chiller',system:'Chiller',exportPath:'Chiller/CH-1'}),
      asset({assetId:'u1',name:'UPS-1',typeId:'UPS',system:'UPS',exportPath:'UPS/UPS-1'}),
      asset({assetId:'n1',name:'SW-1',typeId:'Network Switch',system:'Network Switches',exportPath:'Network Switches/SW-1'}),
    ];
    const first=buildSceneLayout(assets);
    const second=buildSceneLayout([...assets].reverse());
    const byId=(rows:ReturnType<typeof buildSceneLayout>)=>Object.fromEntries(rows.map(row=>[row.asset.assetId,row.position]));
    expect(byId(first)).toEqual(byId(second));
    expect(first.find(x=>x.asset.assetId==='c1')?.areaId).not.toBe(first.find(x=>x.asset.assetId==='u1')?.areaId);
  });

  it('renders only authoritative topology relation kinds',()=>{
    const assets=[
      asset({assetId:'ch',name:'CH',typeId:'Chiller',system:'Chiller',exportPath:'Chiller/CH'}),
      asset({assetId:'crac',name:'CRAC',typeId:'CRAC',system:'CRAC',exportPath:'CRAC/CRAC'}),
    ];
    const placements=buildSceneLayout(assets);
    const topology:Topology={site:{siteId:'DC01'},zones:[],assets,relations:[
      {kind:'serves',from:'ch',to:'crac'},
      {kind:'contains',from:'site:DC01',to:'ch'},
    ]};
    const links=buildSceneLinks(topology,placements,null);
    expect(links).toHaveLength(1);
    expect(links[0].kind).toBe('serves');
  });
});
