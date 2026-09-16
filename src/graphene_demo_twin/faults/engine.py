from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import uuid
from typing import Any

@dataclass
class FaultActivation:
    injectionId: str
    recipeId: str
    targetAsset: str
    severity: float
    simulationTimestamp: str
    source: str = 'INJECTED'
    rampSeconds: float = 0
    durationSeconds: float | None = None
    parameters: dict[str,Any] | None = None

class FaultEngine:
    def __init__(self): self.active: dict[str,FaultActivation]={}
    def inject(self, recipe_id:str,target_asset:str,severity:float,ts:datetime,**kwargs)->FaultActivation:
        if not 0 <= severity <= 1: raise ValueError('severity must be 0..1')
        f=FaultActivation(str(uuid.uuid4()),recipe_id,target_asset,severity,ts.astimezone(timezone.utc).isoformat(),parameters=kwargs or {})
        self.active[f.injectionId]=f; return f
    def update(self,injection_id:str,severity:float):
        if not 0 <= severity <= 1: raise ValueError('severity must be 0..1')
        if injection_id not in self.active: raise KeyError(injection_id)
        self.active[injection_id].severity=severity; return self.active[injection_id]
    def clear(self,injection_id:str): return self.active.pop(injection_id,None)
    def clear_all(self): self.active.clear()
    def list(self): return [asdict(v) for v in self.active.values()]

    def _network_targets(self,target:str,topology:dict|None)->set[str]:
        targets={target}
        if not topology:return targets
        assets=topology.get('assets',[]); by_id={a['assetId']:a for a in assets}
        switch=next((a for a in assets if a['exportPath'].lower()==target.lower()),None)
        if not switch:return targets
        for r in topology.get('relations',[]):
            if r.get('kind')=='networkParent' and r.get('from')==switch['assetId'] and r.get('to') in by_id:
                targets.add(by_id[r['to']]['exportPath'])
        return targets

    def apply(self, snapshot, manifest:dict, scripted:list[FaultActivation]|None=None, topology:dict|None=None):
        values=dict(snapshot.signals); quality={p['signalKey']:'Good' for p in manifest['points']}; site=dict(snapshot.site)
        acts=list(self.active.values())+list(scripted or []); points=manifest['points']
        for f in acts:
            sev=max(0,min(1,f.severity)); target=f.targetAsset.lower(); match_targets={target}
            if f.recipeId=='NETWORK_DEVICE_FAILURE': match_targets={x.lower() for x in self._network_targets(f.targetAsset,topology)}
            matching=[p for p in points if any(t and t in p['exportPath'].lower() for t in match_targets)]
            by_member={p['memberName'].lower():p for p in matching}
            if f.recipeId=='CRAC_VALVE_STUCK':
                for p in matching:
                    n=p['memberName'].lower(); key=p['signalKey']
                    if 'valve command' in n: values[key]=62+(82-62)*sev
                    elif 'valve feedback' in n: values[key]=62+(3-62)*sev
                    elif 'chw flow' in n: values[key]=8.5+(0.3-8.5)*sev
                    elif 'supply air temperature' in n: values[key]=14.4+(22.4-14.4)*sev
                    elif 'filter choke alarm' in n: values[key]=False
                site['hallATempC']=round(site.get('hallATempC',23.1)+1.1*sev,3)
            elif f.recipeId=='CHILLER_CONDENSER_DEGRADATION':
                cop=4.44+(3.89-4.44)*sev
                input_p=None
                for p in matching:
                    n=p['memberName'].lower(); key=p['signalKey']
                    if n=='cop': values[key]=cop
                    elif 'kw per rt' in n: values[key]=3.517/cop
                    elif 'cw flow' in n: values[key]=max(.1,float(values[key])*(1-.28*sev))
                    elif 'cw approach' in n: values[key]=3.2+(5.5-3.2)*sev
                    elif 'condenser pressure' in n: values[key]=float(values[key])*(1+.16*sev)
                    elif n=='input power': values[key]=float(values[key])*(1+.14*sev); input_p=float(values[key])
                if input_p is None and 'input power' in by_member: input_p=float(values[by_member['input power']['signalKey']])
                if input_p is not None and 'cooling output' in by_member: values[by_member['cooling output']['signalKey']]=input_p*cop
            elif f.recipeId=='UPS_RECTIFIER_FAULT':
                for p in matching:
                    n=p['memberName'].lower()
                    if n in {'rectifier failure','battery charging failure','ups common alarm'} or 'rectifier failure' in n: values[p['signalKey']]=True
            elif f.recipeId=='NETWORK_DEVICE_FAILURE':
                for p in matching:
                    n=p['memberName'].lower(); key=p['signalKey']
                    if any(x in n for x in ('comm','status','link status','display status')): values[key]=0
                    elif 'ping' in n: values[key]=9999.0; quality[key]='Bad_CommunicationError'
                    elif any(x in n for x in ('utilization','speed')): values[key]=0
            elif f.recipeId=='WATER_LEAK':
                for p in matching:
                    n=p['memberName'].lower()
                    if 'status' in n: values[p['signalKey']]=1
                    elif 'leak position' in n: values[p['signalKey']]=42.0
                site['hallBRhPct']=round(site.get('hallBRhPct',52)+8*sev,3)
            elif f.recipeId=='PAHU_AFTER_HOURS':
                for p in matching:
                    n=p['memberName'].lower()
                    if n in {'on_off','fan on_off','fan on off'}: values[p['signalKey']]=1
                    elif 'fan speed' in n: values[p['signalKey']]=max(float(values[p['signalKey']]),58+12*sev)
        return {'values':values,'quality':quality,'faults':[asdict(x) for x in acts],'site':site}
