from __future__ import annotations

import asyncio, json, time, uuid, logging
from collections import deque
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from graphene_demo_twin.config import ROOT, world_config, demo_scenarios
from graphene_demo_twin.domain.model import DomainModel, parse_utc
from graphene_demo_twin.faults.engine import FaultEngine, FaultActivation
from graphene_demo_twin.projection.projector import GrapheneProjector

class RuntimeEngine:
    STATES={'STOPPED','STARTING','RUNNING','PAUSED','HOLDING','STOPPING'}
    def __init__(self):
        self.cfg=world_config(); gen=ROOT/'config/generated'
        self.manifest=json.loads((gen/'graphene-coverage-manifest.json').read_text())
        self.topology=json.loads((gen/'graphene-instance-topology.json').read_text())
        self.model=DomainModel(self.manifest,self.topology); self.projector=GrapheneProjector(self.manifest); self.faults=FaultEngine()
        self.overrides={}; self.mode=self.cfg['defaultMode']; self.state='STOPPED'; self.time_scale=self.cfg['demoTimeScale']; self.sim_time=parse_utc(self.cfg['demoStartUtc']); self._wall_anchor=time.monotonic(); self._sim_anchor=self.sim_time
        self.events=deque(maxlen=1000); self.logs=deque(maxlen=2000)
    def _event(self,typ:str,**ctx):
        row={'id':str(uuid.uuid4()),'type':typ,'timestamp':datetime.now(timezone.utc).isoformat(),'simulationTimestamp':self.now().isoformat(),'context':ctx}; self.events.append(row)
        log={'timestamp':row['timestamp'],'level':'INFO','source':'runtime','message':typ,'context':ctx}; self.logs.append(log); logging.getLogger('graphene_demo_twin.runtime').info('%s %s',typ,ctx)
        return row
    def now(self):
        if self.state=='RUNNING':
            current=self._sim_anchor+timedelta(seconds=(time.monotonic()-self._wall_anchor)*self.time_scale)
            if self.mode=='demo' and current>=parse_utc(self.cfg['demoEndUtc']): self.state='HOLDING'; self.sim_time=parse_utc(self.cfg['demoEndUtc']); return self.sim_time
            return current
        return self.sim_time
    def _anchor(self,dt=None): self.sim_time=dt or self.now(); self._sim_anchor=self.sim_time; self._wall_anchor=time.monotonic()
    def start(self):
        if self.state in {'RUNNING','PAUSED','HOLDING'}: return
        self.state='STARTING'; self._anchor(self.sim_time); self.state='RUNNING'; self._event('RUNTIME_STARTED')
    def stop(self): self._anchor(); self.state='STOPPED'; self._event('RUNTIME_STOPPED')
    def pause(self):
        if self.state=='RUNNING': self._anchor(); self.state='PAUSED'; self._event('RUNTIME_PAUSED')
    def resume(self):
        if self.state in {'PAUSED','HOLDING'}: self._anchor(self.sim_time); self.state='RUNNING'; self._event('RUNTIME_RESUMED')
    def reset(self): self._anchor(parse_utc(self.cfg['demoStartUtc']) if self.mode=='demo' else parse_utc(self.cfg['modelEpochUtc'])); self.state='PAUSED'; self._event('CLOCK_RESET')
    def seek(self,dt): self._anchor(parse_utc(dt)); self._event('CLOCK_SEEK')
    def set_time_scale(self,scale:float):
        if scale<=0: raise ValueError('time scale must be >0')
        self._anchor(); self.time_scale=float(scale); self._event('TIME_SCALE_CHANGED',timeScale=scale)
    def set_mode(self,mode:str):
        if mode not in {'demo','open_world'}: raise ValueError('invalid mode')
        self._anchor(); self.mode=mode; self.time_scale=self.cfg['demoTimeScale'] if mode=='demo' else self.cfg['openWorldTimeScale']; self._event('MODE_CHANGED',mode=mode)
    def scripted_faults(self,dt):
        if self.mode!='demo': return []
        out=[]
        for s in demo_scenarios()['scenarios']:
            start=parse_utc(s['startLocal']); end=parse_utc(s['endLocal']) if s.get('endLocal') else None
            if dt<start or (end and dt>=end): continue
            sev=min(1,max(0,(dt-start).total_seconds()/max(1,s.get('rampSeconds',1)))) if s.get('rampSeconds') else 1
            target=self._resolve_target(s)
            out.append(FaultActivation(s['id'],s['recipeId'],target,sev,dt.isoformat(),source='SCRIPTED',rampSeconds=s.get('rampSeconds',0)))
        return out
    def _resolve_target(self,s):
        assets=[a for a in self.topology['assets'] if a['typeId']==s.get('targetType')]
        if s.get('targetMatch'):
            m=s['targetMatch'].lower(); hits=[a for a in assets if m in a['exportPath'].lower()];
            if hits:return hits[0]['exportPath']
        idx=max(0,min(len(assets)-1,s.get('targetOrdinal',1)-1)) if assets else 0
        return assets[idx]['exportPath'] if assets else s.get('targetType','')
    def snapshot(self, include_points=True):
        dt=self.now(); base=self.model.calculate(dt,self.mode); scripted=self.scripted_faults(dt); eff=self.faults.apply(base,self.manifest,scripted,self.topology); points=self.projector.project(base,eff,self.overrides) if include_points else None
        return {'status':self.status(),'site':eff['site'],'faults':eff['faults'],'points':points}
    def status(self): return {'mode':self.mode,'state':self.state,'simulationTimestamp':self.now().isoformat(),'timeScale':self.time_scale,'activeInjectedFaults':len(self.faults.active),'activeOverrides':len(self.overrides)}
