from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib, math
from typing import Any

from graphene_demo_twin.config import world_config, physics_config


def parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime): dt=value
    else: dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None: raise ValueError('timestamp must be timezone-aware')
    return dt.astimezone(timezone.utc)


def deterministic_noise(seed: str, signal: str, dt: datetime, bucket_seconds: int = 60) -> float:
    bucket=int(dt.timestamp())//bucket_seconds
    raw=hashlib.sha256(f"{seed}|{signal}|{bucket}".encode()).digest()
    return int.from_bytes(raw[:8],'big')/(2**64-1)*2-1


def _day_fraction(dt: datetime) -> float:
    return (dt.hour*3600+dt.minute*60+dt.second)/86400


def _interp_profile(hour: float, anchors: list[tuple[float,float]]) -> float:
    pts=anchors+[(anchors[0][0]+24,anchors[0][1])]
    h=hour
    if h < anchors[0][0]: h+=24
    for (h0,v0),(h1,v1) in zip(pts,pts[1:]):
        if h0<=h<=h1:
            f=(h-h0)/(h1-h0); return v0+(v1-v0)*f
    return anchors[-1][1]

@dataclass(frozen=True)
class BaseWorldSnapshot:
    timestamp: datetime
    mode: str
    site: dict[str,Any]
    signals: dict[str,Any]

class DomainModel:
    def __init__(self, manifest: dict, topology: dict, seed: str | None = None):
        self.manifest=manifest; self.topology=topology; self.cfg=world_config(); self.physics=physics_config(); self.seed=seed or self.cfg['seed']
        self.epoch=parse_utc(self.cfg['modelEpochUtc'])

    def calculate(self, timestamp: str|datetime, world_profile: str='demo') -> BaseWorldSnapshot:
        dt=parse_utc(timestamp)
        if dt < self.epoch: raise ValueError(f"timestamp before model epoch {self.epoch.isoformat()}")
        local_hour=(dt.hour+8 + dt.minute/60)%24
        plant_kw=_interp_profile(local_hour,[(0,620),(6,690),(9,820),(13,1030),(15,1080),(19,910),(23,670)])
        slow=1+0.018*math.sin((dt-self.epoch).total_seconds()/86400/7*2*math.pi)
        plant_kw*=slow*(1+0.01*deterministic_noise(self.seed,'site.plant_load',dt,300))
        it_kw=plant_kw*2.35
        outside=29+3.5*math.sin((_day_fraction(dt)-0.25)*2*math.pi)+0.35*deterministic_noise(self.seed,'weather.temp',dt,600)
        hall_a=23.1+0.25*math.sin(_day_fraction(dt)*2*math.pi)+0.08*deterministic_noise(self.seed,'hall.a.temp',dt,300)
        hall_b=23.3+0.23*math.sin((_day_fraction(dt)+.03)*2*math.pi)+0.08*deterministic_noise(self.seed,'hall.b.temp',dt,300)
        hall_a_rh=51.5+1.8*math.sin((_day_fraction(dt)+.1)*2*math.pi)+0.3*deterministic_noise(self.seed,'hall.a.rh',dt,300)
        hall_b_rh=52.0+1.7*math.sin((_day_fraction(dt)+.08)*2*math.pi)+0.3*deterministic_noise(self.seed,'hall.b.rh',dt,300)
        site={'plantLoadKw':round(plant_kw,3),'itLoadKw':round(it_kw,3),'facilityLoadKw':round(it_kw+plant_kw+180,3),'pue':round((it_kw+plant_kw+180)/it_kw,3),'outsideTempC':round(outside,3),'hallATempC':round(hall_a,3),'hallBTempC':round(hall_b,3),'hallARhPct':round(hall_a_rh,3),'hallBRhPct':round(hall_b_rh,3)}
        signals={}
        for p in self.manifest['points']:
            signals[p['signalKey']]=self._value_for(p,dt,site)
        return BaseWorldSnapshot(dt,world_profile,site,signals)

    def _asset_scalar(self, asset: str, low: float, high: float) -> float:
        raw=hashlib.sha256(f"{self.seed}|asset|{asset}".encode()).digest()
        return low+(high-low)*(int.from_bytes(raw[:8],'big')/(2**64-1))

    def _meter_metrics(self, asset: str, dt: datetime) -> dict[str,float]:
        phase=[]
        for i in range(3):
            base=self._asset_scalar(f"{asset}|p{i}",18,115)
            daily=1+0.12*math.sin((_day_fraction(dt)-.18+i*.015)*2*math.pi)
            p=max(.1,base*daily*(1+0.015*deterministic_noise(self.seed,f"{asset}.phase{i}.p",dt,300)))
            pf=0.94+0.018*deterministic_noise(self.seed,f"{asset}.phase{i}.pf",dt,600)
            v=230*(1+0.006*deterministic_noise(self.seed,f"{asset}.phase{i}.v",dt,300))
            s=p/pf; q=math.sqrt(max(0,s*s-p*p)); current=p*1000/max(1,v*pf)
            phase.append({'p':p,'pf':pf,'v':v,'s':s,'q':q,'i':current})
        pt=sum(x['p'] for x in phase); st=sum(x['s'] for x in phase); qt=sum(x['q'] for x in phase)
        return {'P1':phase[0]['p'],'P2':phase[1]['p'],'P3':phase[2]['p'],'Ptot':pt,'Q1':phase[0]['q'],'Q2':phase[1]['q'],'Q3':phase[2]['q'],'Qtot':qt,'S1':phase[0]['s'],'S2':phase[1]['s'],'S3':phase[2]['s'],'Stot':st,'PF1':phase[0]['pf'],'PF2':phase[1]['pf'],'PF3':phase[2]['pf'],'PFsys':pt/st if st else 1,'I1':phase[0]['i'],'I2':phase[1]['i'],'I3':phase[2]['i'],'In':abs(phase[0]['i']-phase[1]['i'])+abs(phase[2]['i']-phase[1]['i']),'Isys':sum(x['i'] for x in phase)/3,'V1':phase[0]['v'],'V2':phase[1]['v'],'V3':phase[2]['v'],'V12':phase[0]['v']*math.sqrt(3),'V23':phase[1]['v']*math.sqrt(3),'V31':phase[2]['v']*math.sqrt(3),'Vsys':sum(x['v'] for x in phase)/3,'Vsys2':sum(x['v'] for x in phase)/3,'Hz':50+0.02*deterministic_noise(self.seed,f"{asset}.hz",dt,300)}

    def _chiller_metrics(self, asset:str, dt:datetime, site:dict) -> dict[str,float]:
        ordinal=max(1, int(self._asset_scalar(asset,1,4)))
        share=site['plantLoadKw']/3*(0.97+0.03*ordinal)
        power=share*(1+0.01*deterministic_noise(self.seed,f"{asset}.power",dt,300))
        cop=4.44+0.025*deterministic_noise(self.seed,f"{asset}.cop",dt,600)
        cooling=power*cop
        return {'Input Power':power,'Load':min(100,100*cooling/1450),'Cooling Output':cooling,'COP':cop,'kW per RT':3.517/cop,'CHW Supply Temperature':5.3+0.05*deterministic_noise(self.seed,f"{asset}.chws",dt,300),'CHW Return Temperature':11.2+0.1*deterministic_noise(self.seed,f"{asset}.chwr",dt,300),'CHW Flow':max(.1,cooling/(4.186*5.9)),'CW Supply Temperature':29.2+0.15*deterministic_noise(self.seed,f"{asset}.cws",dt,300),'CW Return Temperature':33.0+0.18*deterministic_noise(self.seed,f"{asset}.cwr",dt,300),'CW Flow':max(.1,cooling/(4.186*3.8)),'CW Approach':3.2+0.08*deterministic_noise(self.seed,f"{asset}.approach",dt,300),'Condenser Pressure':760+20*deterministic_noise(self.seed,f"{asset}.cond",dt,300)}

    def _energy_integral(self, asset:str, dt:datetime) -> float:
        # Analytic integral of a deterministic baseline + daily sinusoid; O(1) random access.
        hours=(dt-self.epoch).total_seconds()/3600
        base=self._asset_scalar(asset,4,90); amp=.12*base; omega=2*math.pi/24
        phase=self._asset_scalar(asset+'|phase',0,2*math.pi)
        integral=base*hours + amp/omega*(math.cos(phase)-math.cos(omega*hours+phase))
        return max(0,integral)

    def _value_for(self,p:dict,dt:datetime,site:dict)->Any:
        if p.get('sourceClass') in {'STATIC_METADATA','SUPPORT_CONTROL','TEST_SUPPORT'} and p.get('sourceValue') is not None:
            return p['sourceValue']
        n=p['memberName'].lower(); path=p['exportPath'].lower(); dtype=p['dataType']; unit=p.get('engUnit'); noise=deterministic_noise(self.seed,p['signalKey'],dt)
        asset=p.get('instancePath') or p.get('folderPath') or p['exportPath'].rsplit('/',1)[0]
        member=p['memberName']
        if dtype=='Boolean': return False
        if dtype.startswith('Int'):
            if any(k in n for k in ('fault','alarm','failure','trip','trouble')): return 0
            if any(k in n for k in ('on_off','on off','running','run','status','comm','auto_manual','mode','link','admin')): return 1
            return int(max(0,round(10+2*noise)))
        if dtype=='String': return p.get('sourceValue') or p['instanceName'] or ''
        # Meter quantities share one asset-level electrical state.
        if p.get('typeId') in {'GPM96','GPQM144','GPQM96','GEM230','GEM630','GEM630-CT-L','Production/GDC230','Production/GPQM144 Pro','Production/GPM96','Production/GEM130','Production/GEM630','Production/E820','Production/GEM230','Production/GPQM96'}:
            mm=self._meter_metrics(asset,dt)
            if member in mm: return round(mm[member],4)
            if member.startswith('THD'): return round(2.2+0.8*noise,3)
            if member=='Wh_Im': return round(self._energy_integral(asset,dt),3)
        if p.get('typeId')=='BCPM':
            watts=self._asset_scalar(asset,2.5,14.0)*(1+0.12*math.sin(_day_fraction(dt)*2*math.pi))
            if n=='active power': return round(watts,3)
            if n=='current': return round(watts*1000/(230*.95),3)
            if 'accumulated energy' in n: return round(self._energy_integral(asset,dt),3)
        if p.get('typeId')=='Chiller':
            cm=self._chiller_metrics(asset,dt,site)
            if member in cm: return round(cm[member],4)
        if 'supply air temperature' in n or n in {'sat'}: return round((14.4 if 'crac' in path else 15.2)+0.25*noise,3)
        if 'return air temperature' in n or n in {'rat'}: return round(24.1+0.3*noise,3)
        if 'humidity' in n or '%rh'==(unit or '').lower(): return round(52+2*noise,3)
        if 'temperature' in n or unit=='°C':
            if 'outside' in n: return site['outsideTempC']
            if 'chw supply' in n or 'chws' in n: return round(5.3+0.08*noise,3)
            if 'chw return' in n or 'chwr' in n: return round(11.2+0.15*noise,3)
            if 'cw supply' in n or 'cws' in n: return round(29.2+0.2*noise,3)
            if 'cw return' in n or 'cwr' in n: return round(33.0+0.25*noise,3)
            return round(23.2+0.6*noise,3)
        if 'energy' in n or 'wh_im' in n or 'accumulated' in n: return round(self._energy_integral(asset,dt),3)
        if unit in {'kW','kVA','kVAR'} or 'power' in n or n in {'ptot','p1','p2','p3'}:
            base=max(0.1,site['facilityLoadKw']/max(1,self.manifest['sourceCounts'].get('udtInstances',831))*4.8)
            if 'chiller' in path: base=site['plantLoadKw']/3
            if 'cooling tower' in path: base=18+6*((site['outsideTempC']-25)/8)
            if 'ups' in path: base=site['itLoadKw']/25
            if unit=='kVA': base/=0.94
            if unit=='kVAR': base*=0.36
            return round(max(0,base*(1+0.04*noise)),3)
        if unit=='V' or 'voltage' in n: return round((400 if any(x in n for x in ('l1-l2','l2-l3','l3-l1','v12','v23','v31')) else 230)*(1+0.008*noise),3)
        if unit=='A' or 'current' in n: return round(max(0,42*(1+0.18*noise)),3)
        if unit=='Hz' or 'frequency' in n: return round(50+0.035*noise,3)
        if unit in {'%','%RH'} or any(k in n for k in ('load','speed','position','capacity','utilization','soc')): return round(min(100,max(0,62+8*noise)),3)
        if 'flow' in n: return round(max(.05,8.5*(1+0.12*noise)),3)
        if 'pressure' in n: return round(max(0,180*(1+0.08*noise)),3)
        if n=='cop': return round(4.44+0.03*noise,3)
        if 'kw per rt' in n: return round(3.517/4.44,3)
        if p.get('sourceValue') is not None: return p['sourceValue']
        return round(10+noise,3)
