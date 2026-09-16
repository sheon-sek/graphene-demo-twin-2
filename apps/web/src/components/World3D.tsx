import {useEffect,useMemo,useRef,useState} from 'react';
import {Canvas,useFrame,useThree} from '@react-three/fiber';
import {Grid,Html,Line,OrbitControls} from '@react-three/drei';
import type {Asset,FaultActivation,Topology} from '../lib/api';
import {SCENE_AREAS,buildSceneLayout,buildSceneLinks,type SceneAreaId,type SceneLayer,type ScenePlacement,type Vec3} from '../lib/scene';
import {Color,DoubleSide,Vector3} from 'three';

const LAYER_META: Record<SceneLayer,{label:string;color:string}> = {
  cooling:{label:'Cooling plant',color:'#5b9fb0'},
  airside:{label:'Airside',color:'#5c9b91'},
  power:{label:'Power',color:'#a9915b'},
  network:{label:'Network',color:'#7e79a7'},
  water:{label:'Water / utility',color:'#587fa5'},
  liquid:{label:'Liquid cooling',color:'#5a9aa0'},
  safety:{label:'Safety',color:'#9d7268'},
  environment:{label:'Environment',color:'#6f9472'},
};

const DEFAULT_LAYERS: Record<SceneLayer,boolean> = {cooling:true,airside:true,power:true,network:true,water:true,liquid:true,safety:true,environment:false};
const VIEW_AREAS: Array<{id:'all'|SceneAreaId;label:string}> = [
  {id:'all',label:'All'},
  {id:'cooling',label:'Cooling'},
  {id:'hall',label:'Data Hall'},
  {id:'electrical',label:'Electrical'},
  {id:'network',label:'Network'},
  {id:'utility',label:'Utility'},
  {id:'liquid',label:'Liquid'},
];

function paint(layer:SceneLayer,{faulted,selected,hovered}:{faulted:boolean;selected:boolean;hovered:boolean}){
  if(faulted) return '#d85e55';
  if(selected) return '#7ab8c8';
  if(hovered) return new Color(LAYER_META[layer].color).offsetHSL(0,0,.08).getStyle();
  return LAYER_META[layer].color;
}

function Metal({color,emissive='#000000',emissiveIntensity=0}:{color:string;emissive?:string;emissiveIntensity?:number}){
  return <meshStandardMaterial color={color} roughness={0.56} metalness={0.28} emissive={emissive} emissiveIntensity={emissiveIntensity}/>;
}
function DarkMetal(){return <meshStandardMaterial color="#26323a" roughness={0.72} metalness={0.34}/>}
function Screen(){return <meshStandardMaterial color="#78949b" roughness={0.35} metalness={0.08} emissive="#2e4d53" emissiveIntensity={.62}/>}

function ChillerModel({color}:{color:string}){return <group>
  <mesh castShadow receiveShadow position={[0,.38,0]} scale={[1.55,.7,.82]}><boxGeometry/><Metal color={color}/></mesh>
  <mesh castShadow position={[-.48,.42,.48]} rotation={[0,0,Math.PI/2]} scale={[.42,.42,.42]}><cylinderGeometry args={[.46,.46,1,18]}/><DarkMetal/></mesh>
  <mesh castShadow position={[.46,.43,.48]} rotation={[0,0,Math.PI/2]} scale={[.32,.32,.32]}><cylinderGeometry args={[.42,.42,1,18]}/><DarkMetal/></mesh>
  <mesh position={[0,.56,-.416]} scale={[.55,.14,.018]}><boxGeometry/><Screen/></mesh>
  <mesh castShadow position={[-.62,.22,-.52]} rotation={[Math.PI/2,0,0]}><cylinderGeometry args={[.07,.07,.55,12]}/><meshStandardMaterial color="#668a91" metalness={.55} roughness={.35}/></mesh>
  <mesh castShadow position={[.62,.22,-.52]} rotation={[Math.PI/2,0,0]}><cylinderGeometry args={[.07,.07,.55,12]}/><meshStandardMaterial color="#668a91" metalness={.55} roughness={.35}/></mesh>
</group>}

function PumpModel({color}:{color:string}){return <group>
  <mesh castShadow position={[-.28,.32,0]} rotation={[0,0,Math.PI/2]} scale={[.46,.46,.46]}><cylinderGeometry args={[.38,.38,.95,18]}/><Metal color={color}/></mesh>
  <mesh castShadow position={[.32,.32,0]} rotation={[0,0,Math.PI/2]} scale={[.42,.42,.42]}><cylinderGeometry args={[.46,.46,.55,18]}/><DarkMetal/></mesh>
  <mesh castShadow position={[.62,.32,0]} rotation={[0,0,Math.PI/2]}><cylinderGeometry args={[.08,.08,.48,12]}/><meshStandardMaterial color="#68838b" metalness={.5} roughness={.38}/></mesh>
  <mesh castShadow position={[0,.07,0]} scale={[1.25,.12,.72]}><boxGeometry/><DarkMetal/></mesh>
</group>}

function ValveModel({color}:{color:string}){return <group>
  <mesh castShadow position={[0,.28,0]} rotation={[0,0,Math.PI/2]}><cylinderGeometry args={[.13,.13,1.05,14]}/><meshStandardMaterial color="#6c858c" metalness={.55} roughness={.38}/></mesh>
  <mesh castShadow position={[0,.3,0]} scale={[.48,.48,.48]}><sphereGeometry args={[.42,18,12]}/><Metal color={color}/></mesh>
  <mesh castShadow position={[0,.72,0]}><cylinderGeometry args={[.055,.055,.55,10]}/><DarkMetal/></mesh>
  <mesh castShadow position={[0,1.02,0]} rotation={[Math.PI/2,0,0]}><torusGeometry args={[.25,.045,8,20]}/><meshStandardMaterial color="#88969a" metalness={.48} roughness={.4}/></mesh>
</group>}

function TowerModel({color}:{color:string}){return <group>
  <mesh castShadow receiveShadow position={[0,.42,0]}><cylinderGeometry args={[.58,.83,.78,10]}/><Metal color={color}/></mesh>
  <mesh castShadow receiveShadow position={[0,1.07,0]}><cylinderGeometry args={[.83,.58,.54,10]}/><Metal color={color}/></mesh>
  <mesh castShadow position={[0,1.39,0]}><cylinderGeometry args={[.62,.62,.12,18]}/><DarkMetal/></mesh>
  <mesh position={[0,1.46,0]} rotation={[-Math.PI/2,0,0]}><torusGeometry args={[.33,.035,8,24]}/><meshStandardMaterial color="#8ba0a4" metalness={.38} roughness={.5}/></mesh>
</group>}

function AirHandlerModel({color,type}:{color:string;type:string}){const crac=type.toLowerCase()==='crac'; const long=type.toLowerCase()==='pahu'; return <group>
  <mesh castShadow receiveShadow position={[0,crac ? .65 : .45,0]} scale={[long?1.35:.82,crac?1.3:.9,.72]}><boxGeometry/><Metal color={color}/></mesh>
  <mesh position={[0,crac ? .72 : .48,.365]} scale={[long ? .9 : .5,.48,.018]}><boxGeometry/><DarkMetal/></mesh>
  {[-.22,0,.22].map((x,i)=><mesh key={i} position={[long?x*2:x,crac ? .72 : .48,.387]} scale={[long ? .42 : .16,.035,.012]}><boxGeometry/><meshBasicMaterial color="#60777e"/></mesh>)}
  <mesh position={[long ? .48 : 0,crac?1.12:.72,.39]} scale={[.18,.09,.015]}><boxGeometry/><Screen/></mesh>
</group>}

function UpsModel({color}:{color:string}){return <group>
  <mesh castShadow receiveShadow position={[0,.82,0]} scale={[.78,1.64,.68]}><boxGeometry/><Metal color={color}/></mesh>
  <mesh position={[0,1.02,.347]} scale={[.35,.18,.012]}><boxGeometry/><Screen/></mesh>
  <mesh position={[0,.42,.35]} scale={[.48,.22,.012]}><boxGeometry/><DarkMetal/></mesh>
  {[-.16,0,.16].map((x,i)=><mesh key={i} position={[x,.42,.364]} scale={[.08,.025,.01]}><boxGeometry/><meshBasicMaterial color="#5d7077"/></mesh>)}
</group>}

function GensetModel({color}:{color:string}){return <group>
  <mesh castShadow receiveShadow position={[0,.48,0]} scale={[1.5,.88,.78]}><boxGeometry/><Metal color={color}/></mesh>
  <mesh position={[-.3,.56,.397]} scale={[.55,.35,.012]}><boxGeometry/><DarkMetal/></mesh>
  <mesh castShadow position={[.48,1.2,.08]}><cylinderGeometry args={[.075,.09,.68,12]}/><meshStandardMaterial color="#5f6b6f" metalness={.5} roughness={.5}/></mesh>
  <mesh castShadow position={[.48,1.55,.08]} rotation={[0,0,Math.PI/2]}><cylinderGeometry args={[.085,.085,.32,12]}/><meshStandardMaterial color="#5f6b6f" metalness={.5} roughness={.5}/></mesh>
</group>}

function NetworkModel({color,switchLike}:{color:string;switchLike:boolean}){return <group>
  <mesh castShadow receiveShadow position={[0,.2,0]} scale={[switchLike?1.12:.88,.3,.66]}><boxGeometry/><Metal color={color}/></mesh>
  <mesh position={[0,.2,.337]} scale={[switchLike ? .82 : .6,.12,.012]}><boxGeometry/><DarkMetal/></mesh>
  {Array.from({length:switchLike?8:5},(_,i)=>{const count=switchLike?8:5; const x=(i-(count-1)/2)*(switchLike ? .19 : .2); return <mesh key={i} position={[x,.2,.351]} scale={[.055,.035,.008]}><boxGeometry/><meshBasicMaterial color={i%3===0?'#76a58b':'#566b73'}/></mesh>})}
</group>}

function PanelModel({color}:{color:string}){return <group>
  <mesh castShadow receiveShadow position={[0,.55,0]} scale={[.58,1.08,.42]}><boxGeometry/><Metal color={color}/></mesh>
  <mesh position={[0,.72,.217]} scale={[.28,.16,.012]}><boxGeometry/><Screen/></mesh>
  <mesh position={[0,.35,.218]} scale={[.32,.25,.01]}><boxGeometry/><DarkMetal/></mesh>
</group>}

function TankModel({color}:{color:string}){return <group>
  <mesh castShadow receiveShadow position={[0,.72,0]}><cylinderGeometry args={[.48,.52,1.35,20]}/><Metal color={color}/></mesh>
  {[.24,.72,1.2].map((y,i)=><mesh key={i} position={[0,y,0]}><torusGeometry args={[.505,.025,8,24]}/><meshStandardMaterial color="#71858a" metalness={.45} roughness={.42}/></mesh>)}
  <mesh castShadow position={[0,1.43,0]}><cylinderGeometry args={[.18,.18,.18,16]}/><DarkMetal/></mesh>
</group>}

function CduModel({color}:{color:string}){return <group>
  <mesh castShadow receiveShadow position={[0,.72,0]} scale={[.8,1.4,.72]}><boxGeometry/><Metal color={color}/></mesh>
  <mesh position={[0,.92,.367]} scale={[.34,.18,.012]}><boxGeometry/><Screen/></mesh>
  {[-.25,.25].map((x,i)=><mesh key={i} castShadow position={[x,.25,-.52]} rotation={[Math.PI/2,0,0]}><cylinderGeometry args={[.07,.07,.75,12]}/><meshStandardMaterial color={i?'#8a6962':'#5e8c99'} metalness={.42} roughness={.42}/></mesh>)}
</group>}

function SensorModel({color,faulted}:{color:string;faulted:boolean}){return <group>
  <mesh castShadow position={[0,.26,0]}><cylinderGeometry args={[.04,.055,.48,10]}/><DarkMetal/></mesh>
  <mesh castShadow position={[0,.54,0]} scale={[.16,.16,.16]}><sphereGeometry args={[.5,12,8]}/><meshStandardMaterial color={color} roughness={.4} emissive={faulted?'#6f201d':'#16261d'} emissiveIntensity={faulted?1.5:.2}/></mesh>
</group>}

function GenericModel({color}:{color:string}){return <mesh castShadow receiveShadow position={[0,.38,0]} scale={[.62,.72,.58]}><boxGeometry/><Metal color={color}/></mesh>}

function ModelForAsset({placement,color,faulted}:{placement:ScenePlacement;color:string;faulted:boolean}){
  switch(placement.family){
    case 'chiller': return <ChillerModel color={color}/>;
    case 'pump': return <PumpModel color={color}/>;
    case 'valve': return <ValveModel color={color}/>;
    case 'tower': return <TowerModel color={color}/>;
    case 'air-handler': return <AirHandlerModel color={color} type={placement.asset.typeId}/>;
    case 'ups': return <UpsModel color={color}/>;
    case 'genset': return <GensetModel color={color}/>;
    case 'network-switch': return <NetworkModel color={color} switchLike/>;
    case 'server': return <NetworkModel color={color} switchLike={false}/>;
    case 'panel': return <PanelModel color={color}/>;
    case 'tank': return <TankModel color={color}/>;
    case 'cdu': return <CduModel color={color}/>;
    case 'sensor': return <SensorModel color={color} faulted={faulted}/>;
    default: return <GenericModel color={color}/>;
  }
}

function FaultBeacon(){return <mesh position={[0,.035,0]} rotation={[-Math.PI/2,0,0]}><ringGeometry args={[.62,.72,36]}/><meshBasicMaterial color="#e3665e" transparent opacity={.92} side={DoubleSide}/></mesh>}
function SelectionRing(){return <mesh position={[0,.028,0]} rotation={[-Math.PI/2,0,0]}><ringGeometry args={[.68,.735,40]}/><meshBasicMaterial color="#8bd0dc" transparent opacity={.92} side={DoubleSide}/></mesh>}

function AssetObject({placement,faulted,selected,onSelect}:{placement:ScenePlacement;faulted:boolean;selected:boolean;onSelect:(asset:Asset)=>void}){
  const [hovered,setHovered]=useState(false);
  const color=paint(placement.layer,{faulted,selected,hovered});
  return <group position={placement.position} onClick={e=>{e.stopPropagation();onSelect(placement.asset)}} onPointerOver={e=>{e.stopPropagation();setHovered(true);document.body.style.cursor='pointer'}} onPointerOut={()=>{setHovered(false);document.body.style.cursor='default'}}>
    <ModelForAsset placement={placement} color={color} faulted={faulted}/>
    {selected&&<SelectionRing/>}{faulted&&<FaultBeacon/>}
    {(hovered||selected)&&<Html position={[0,1.72,0]} center distanceFactor={10} style={{pointerEvents:'none'}}><div className={`sceneTooltip ${faulted?'fault':''}`}><b>{placement.asset.name}</b><span>{placement.asset.typeId}</span>{faulted&&<em>FAULT SOURCE</em>}</div></Html>}
  </group>;
}

function AreaPad({areaId}:{areaId:SceneAreaId}){const area=SCENE_AREAS[areaId]; const x=area.size[0],z=area.size[1]; const corners:Vec3[]=[[-x/2,.015,-z/2],[x/2,.015,-z/2],[x/2,.015,z/2],[-x/2,.015,z/2],[-x/2,.015,-z/2]]; return <group position={[area.center[0],0,area.center[1]]}>
  <mesh receiveShadow position={[0,-.06,0]} scale={[x,.1,z]}><boxGeometry/><meshStandardMaterial color="#11191e" roughness={.95} metalness={.02}/></mesh>
  <Line points={corners} color={area.accent} transparent opacity={.48} lineWidth={1}/>
  <Html position={[-x/2+.35,.08,-z/2+.35]} distanceFactor={14} style={{pointerEvents:'none'}}><div className="sceneAreaTag"><i style={{background:area.accent}}/><span>{area.label}</span></div></Html>
</group>}

function TopologyLinks({topology,placements,selectedAssetId,visible}:{topology:Topology|null;placements:ScenePlacement[];selectedAssetId?:string|null;visible:boolean}){
  const links=useMemo(()=>visible?buildSceneLinks(topology,placements,selectedAssetId):[],[topology,placements,selectedAssetId,visible]);
  return <>{links.map(link=>{const color=link.kind==='networkParent'?'#8179a8':link.kind==='feeds'?'#b3985e':'#5c9bac';return <Line key={link.id} points={link.points} color={color} transparent opacity={selectedAssetId ? .6 : .18} lineWidth={selectedAssetId?1.7:.75}/>})}</>;
}

const ALL_CAMERA={position:new Vector3(30,23,31),target:new Vector3(.5,0,.5)};
function presetCamera(view:'all'|SceneAreaId){if(view==='all')return ALL_CAMERA; const area=SCENE_AREAS[view]; const span=Math.max(...area.size); return {position:new Vector3(area.center[0]+span*.72,Math.max(8,span*.78),area.center[1]+span*.78),target:new Vector3(area.center[0],.45,area.center[1])}}

function CameraController({focus,view}:{focus?:Vec3;view:'all'|SceneAreaId}){
  const {camera,invalidate}=useThree(); const controls=useRef<any>(null); const animating=useRef(true); const goalPosition=useRef(ALL_CAMERA.position.clone()); const goalTarget=useRef(ALL_CAMERA.target.clone());
  useEffect(()=>{if(focus){goalTarget.current.set(focus[0],.58,focus[2]);goalPosition.current.set(focus[0]+4.1,3.4,focus[2]+4.1)}else{const preset=presetCamera(view);goalPosition.current.copy(preset.position);goalTarget.current.copy(preset.target)}animating.current=true;invalidate()},[focus?.[0],focus?.[2],view,invalidate]);
  useFrame(()=>{if(!animating.current||!controls.current)return; camera.position.lerp(goalPosition.current,.12); controls.current.target.lerp(goalTarget.current,.14); controls.current.update(); const done=camera.position.distanceTo(goalPosition.current)<.03&&controls.current.target.distanceTo(goalTarget.current)<.03; if(done)animating.current=false; else invalidate()});
  return <OrbitControls ref={controls} makeDefault enableDamping dampingFactor={.08} minDistance={3} maxDistance={58} maxPolarAngle={Math.PI*.485} onStart={()=>{animating.current=false}}/>;
}

function Scene({placements,topology,faults,selectedAssetId,onSelect,layers,view,showConnections}:{placements:ScenePlacement[];topology:Topology|null;faults:FaultActivation[];selectedAssetId?:string|null;onSelect:(a:Asset)=>void;layers:Record<SceneLayer,boolean>;view:'all'|SceneAreaId;showConnections:boolean}){
  const visible=useMemo(()=>placements.filter(p=>(layers[p.layer]||selectedAssetId===p.asset.assetId)&&(view==='all'||p.areaId===view||selectedAssetId===p.asset.assetId)),[placements,layers,view,selectedAssetId]);
  const visibleIds=useMemo(()=>new Set(visible.map(p=>p.asset.assetId)),[visible]);
  const linkPlacements=useMemo(()=>placements.filter(p=>visibleIds.has(p.asset.assetId)),[placements,visibleIds]);
  const faultPaths=useMemo(()=>new Set(faults.map(f=>f.targetAsset)),[faults]);
  const focus=selectedAssetId?placements.find(p=>p.asset.assetId===selectedAssetId)?.position:undefined;
  return <>
    <color attach="background" args={['#0a1014']}/><fog attach="fog" args={['#0a1014',28,54]}/>
    <hemisphereLight intensity={.7} color="#c7d7df" groundColor="#182027"/>
    <directionalLight castShadow position={[11,18,9]} intensity={2.2} color="#e2ebef" shadow-mapSize-width={1024} shadow-mapSize-height={1024} shadow-camera-left={-24} shadow-camera-right={24} shadow-camera-top={20} shadow-camera-bottom={-20} shadow-camera-near={1} shadow-camera-far={58} shadow-normalBias={.015}/>
    <Grid args={[52,32]} position={[.5,-.12,.5]} cellSize={1} cellThickness={.35} cellColor="#28343b" sectionSize={5} sectionThickness={.65} sectionColor="#34434c" fadeDistance={46} fadeStrength={1.2}/>
    {(Object.keys(SCENE_AREAS) as SceneAreaId[]).map(id=><AreaPad key={id} areaId={id}/>)}
    <TopologyLinks topology={topology} placements={linkPlacements} selectedAssetId={selectedAssetId} visible={showConnections}/>
    {visible.map(p=><AssetObject key={p.asset.assetId} placement={p} selected={selectedAssetId===p.asset.assetId} faulted={faultPaths.has(p.asset.exportPath)} onSelect={onSelect}/>)}
    <CameraController focus={focus} view={view}/>
  </>;
}

export function World3D({assets,topology,faults=[],selectedAssetId,onSelect}:{assets:Asset[];topology:Topology|null;faults?:FaultActivation[];selectedAssetId?:string|null;onSelect:(a:Asset)=>void}){
  const placements=useMemo(()=>buildSceneLayout(assets),[assets]);
  const [layers,setLayers]=useState<Record<SceneLayer,boolean>>(DEFAULT_LAYERS);
  const [view,setView]=useState<'all'|SceneAreaId>('all');
  const [showConnections,setShowConnections]=useState(true);
  const activeLayerCount=Object.values(layers).filter(Boolean).length;
  const visibleCount=placements.filter(p=>(layers[p.layer]||selectedAssetId===p.asset.assetId)&&(view==='all'||p.areaId===view||selectedAssetId===p.asset.assetId)).length;
  const toggle=(layer:SceneLayer)=>setLayers(current=>({...current,[layer]:!current[layer]}));
  return <div className="worldStage">
    <div className="worldToolbar" aria-label="3D scene controls">
      <div className="toolbarGroup"><span>VIEW</span>{VIEW_AREAS.map(item=><button key={item.id} className={view===item.id?'active':''} onClick={()=>setView(item.id)}>{item.label}</button>)}</div>
      <div className="toolbarGroup layers"><span>LAYERS</span>{(Object.keys(LAYER_META) as SceneLayer[]).map(layer=><button key={layer} className={layers[layer]?'active':''} onClick={()=>toggle(layer)} aria-pressed={layers[layer]}><i style={{background:LAYER_META[layer].color}}/>{LAYER_META[layer].label}</button>)}</div>
      <button className={`connectionToggle ${showConnections?'active':''}`} onClick={()=>setShowConnections(v=>!v)} aria-pressed={showConnections}>Connections</button>
    </div>
    <div className="worldHud"><span><b>{visibleCount}</b> assets</span><span><b>{activeLayerCount}</b> layers</span><span><b>{faults.length}</b> fault sources</span></div>
    <div className="worldCanvas">
      <Canvas shadows frameloop="demand" dpr={[1,1.35]} camera={{position:[30,23,31],fov:42,near:.1,far:110}} gl={{antialias:true,powerPreference:'high-performance'}}>
        <Scene placements={placements} topology={topology} faults={faults} selectedAssetId={selectedAssetId} onSelect={onSelect} layers={layers} view={view} showConnections={showConnections}/>
      </Canvas>
    </div>
    <div className="worldLegend"><span className="legendFault"><i/>Fault source</span><span className="legendSelected"><i/>Selected</span><small>Orbit · pan · zoom · select · choose a view to frame an area</small></div>
  </div>;
}
