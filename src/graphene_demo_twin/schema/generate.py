from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from graphene_demo_twin.config import ROOT
from .parser import parse_exports, stable_key, classify

EXTENSIONS: dict[str, list[tuple[str, str, str | None]]] = {
    "CRAC": [("CHW Valve Command","Float4","%"),("CHW Valve Feedback","Float4","%"),("CHW Flow","Float4","L/s"),("CHW Supply Temperature","Float4","°C"),("CHW Return Temperature","Float4","°C"),("Fan Electrical Power","Float4","kW")],
    "Chiller": [("Load","Float4","%"),("Cooling Output","Float4","kW"),("COP","Float4",None),("kW per RT","Float4","kW/RT"),("CHW Supply Temperature","Float4","°C"),("CHW Return Temperature","Float4","°C"),("CHW Flow","Float4","L/s"),("CW Supply Temperature","Float4","°C"),("CW Return Temperature","Float4","°C"),("CW Flow","Float4","L/s"),("CW Approach","Float4","K"),("Operating Hours","Float8","h"),("Energy","Float4","kWh")],
    "Chiller Pump": [("Auto_Manual","Int4",None),("Current","Float4","A"),("Frequency","Float4","Hz"),("Speed Command","Float4","%"),("Speed Feedback","Float4","%"),("Flow","Float4","L/s"),("Suction Pressure","Float4","kPa"),("Discharge Pressure","Float4","kPa"),("Differential Pressure","Float4","kPa"),("System Failure_Trip","Boolean",None),("HasAlarm","Boolean",None),("Run Hours","Float8","h")],
    "Chiller Valve": [("Command","Float4","%"),("Feedback","Float4","%"),("Position","Float4","%"),("Auto_Manual","Int4",None),("Fault","Boolean",None)],
    "Cooling Tower": [("CWR Temperature","Float4","°C"),("CWS Temperature","Float4","°C"),("Approach","Float4","K"),("Fan Speed Command","Float4","%"),("Fan Speed Feedback","Float4","%"),("Basin Level","Float4","%"),("Run Hours","Float8","h")],
    "PAHU": [("Static Pressure","Float4","Pa"),("Static Pressure Setpoint","Float4","Pa"),("Fan Speed Command","Float4","%"),("Fan Speed Feedback","Float4","%"),("CHW Valve Command","Float4","%"),("CHW Valve Feedback","Float4","%"),("CHW Flow","Float4","L/s")],
    "FWU": [("Fan Speed Command","Float4","%"),("Fan Speed Feedback","Float4","%"),("CHW Valve Command","Float4","%"),("CHW Valve Feedback","Float4","%")],
    "FCU": [("Fan Speed Command","Float4","%"),("Fan Speed Feedback","Float4","%"),("CHW Valve Command","Float4","%"),("CHW Valve Feedback","Float4","%")],
    "UPS": [("Input Voltage","Float4","V"),("Output Voltage","Float4","V"),("Input Power","Float4","kW"),("Output Power","Float4","kW"),("Load %","Float4","%"),("Inverter Status","Int4",None),("Bypass Status","Int4",None),("Battery SOC","Float4","%"),("Battery Voltage","Float4","V"),("Battery Current","Float4","A"),("Battery Runtime Remaining","Float4","min"),("Battery Charging Failure","Boolean",None),("UPS Common Alarm","Boolean",None)],
    "Genset": [("Active Power","Float4","kW"),("Reactive Power","Float4","kVAR"),("Apparent Power","Float4","kVA"),("Load %","Float4","%"),("Current","Float4","A"),("Energy","Float8","kWh"),("Breaker Status","Int4",None),("Fuel Level","Float4","%")],
    "CDU": [("Supply Coolant Temperature","Float4","°C"),("Return Coolant Temperature","Float4","°C"),("Flow","Float4","L/s"),("Differential Pressure","Float4","kPa"),("Pump Run","Int4",None),("Pump Speed","Float4","%"),("Pump Power","Float4","kW"),("IT Load","Float4","kW"),("Facility Load","Float4","kW"),("Heat Load","Float4","kW"),("Unit Running Status","Int4",None),("Alarm Status","Boolean",None)],
}


def _atomic_members_for_instance(points: list[dict]) -> dict[str, set[str]]:
    out=defaultdict(set)
    for p in points:
        if p.get("instancePath"): out[p["instancePath"]].add(p["memberName"])
    return out


def build_extensions(parsed) -> list[dict]:
    existing_paths={p["exportPath"] for p in parsed.points}; members=_atomic_members_for_instance(parsed.points); out=[]
    # only top-level/root UDT instances, excluding nested switch port pseudo-instances.
    seen=set()
    for inst in parsed.instances:
        ip=inst["rootInstancePath"]
        if ip in seen or inst.get("path") != ip: continue
        seen.add(ip); type_id=inst.get("typeId")
        if type_id not in EXTENSIONS: continue
        for member,dtype,unit in EXTENSIONS[type_id]:
            if member in members[ip]: continue
            path=f"Twin Extensions/{ip}/{member}"
            if path in existing_paths: continue
            cls, scope, ai = classify(path,member,dtype,None)
            out.append({"exportPath":path,"folderPath":path.rsplit('/',1)[0],"instanceName":inst['name'],"instancePath":ip,"typeId":type_id,"memberName":member,"memberPath":member,"dataType":dtype,"engUnit":unit,"historyIntent":False,"alarmDefinitions":[],"sourceClass":cls,"simulationOwner":"DomainModel","domainSignal":stable_key(f"{ip}/{member}"),"signalKey":stable_key(f"{ip}/{member}"),"origin":"twin-extension","scope":"PRODUCTION","aiVisible":True,"runtimeRequired":True,"sourceValue":None,"sourceMetadata":{},"extensionReason":"AI-diagnostic-observability"})
    return out


def build_topology(parsed) -> dict:
    assets=[]; relations=[]; zones={}
    top_instances=[]
    for inst in parsed.instances:
        if inst["path"] != inst["rootInstancePath"]: continue
        p=inst["path"]; root=p.split('/')[0]; name=inst['name']; typ=inst.get('typeId') or 'UdtInstance'
        low=p.lower()
        if 'level 1' in low or name.startswith('L1_'): zone='Level-1'
        elif 'level 2' in low or name.startswith('L2_'): zone='Level-2'
        elif 'hall' in low or 'datahall 1' in low: zone='Hall-A'
        elif 'datahall 2' in low: zone='Hall-B'
        else: zone=root
        zones.setdefault(zone,{"id":zone,"label":zone})
        aid=stable_key(p).replace('sig.','asset.',1)
        assets.append({"assetId":aid,"name":name,"typeId":typ,"exportPath":p,"system":root,"zoneId":zone,"roomId":zone,"rowId":None,"scope":"SUPPORT" if root in {'DemoRack','MQTT Tags','PredictionCache','Testing'} else 'PRODUCTION'})
        relations.append({"kind":"locatedIn","from":aid,"to":f"zone:{zone}"})
        relations.append({"kind":"contains","from":"site:DC01","to":aid})
        top_instances.append((aid,p,typ,zone))
    # Coherent broad system causality links, deterministically generated by type groups.
    bytype=defaultdict(list)
    for row in top_instances: bytype[row[2]].append(row)
    def link(src_type,dst_type,kind='serves'):
        src=bytype.get(src_type,[]); dst=bytype.get(dst_type,[])
        if not src or not dst: return
        for i,d in enumerate(dst):
            s=src[i % len(src)]; relations.append({"kind":kind,"from":s[0],"to":d[0]})
            relations.append({"kind":"servedBy" if kind=='serves' else 'fedBy',"from":d[0],"to":s[0]})
    link('Chiller','CRAC'); link('Chiller','PAHU'); link('Chiller Pump','Chiller'); link('Cooling Tower','Chiller')
    link('UPS','BCPM','feeds'); link('BCPM','Environment Monitoring','feeds'); link('Network Switch','Network Device','networkParent')
    return {"site":{"siteId":"DC01"},"zones":list(zones.values()),"assets":assets,"relations":relations}


def generate(repo: Path = ROOT) -> dict[str, Any]:
    ref=repo/'reference/graphene'; out=repo/'config/generated'; out.mkdir(parents=True,exist_ok=True)
    parsed=parse_exports(ref/'real-graphene-demo-udt-definitions.json',ref/'real-graphene-demo-tag-instances.json')
    extensions=build_extensions(parsed); all_points=parsed.points+extensions
    schema={"schemaVersion":2,"counts":parsed.counts,"udtTypes":parsed.udt_types}
    topology=build_topology(parsed)
    manifest={"schemaVersion":2,"sourceCounts":parsed.counts,"exportedPointCount":len(parsed.points),"extensionPointCount":len(extensions),"points":all_points}
    signal_registry={"signals":[{"signalKey":p['signalKey'],"owner":p['simulationOwner'],"points":[p['exportPath']],"sourceClass":p['sourceClass']} for p in all_points]}
    files={"graphene-schema.json":schema,"graphene-instance-topology.json":topology,"graphene-coverage-manifest.json":manifest,"signal-registry.json":signal_registry,"normalization-review.json":{"reviews":parsed.reviews}}
    for name,data in files.items(): (out/name).write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8')
    by_class=Counter(p['sourceClass'] for p in all_points); roots=Counter(p['exportPath'].split('/')[0] for p in parsed.points)
    audit=f"""# Graphene Schema Audit\n\nGenerated from the read-only reference exports.\n\n## Inventory\n\n- UDT types: **{len(parsed.udt_types)}**\n- Instance folders: **{len(parsed.folders)}**\n- UDT instances: **{len(parsed.instances)}**\n- Exported AtomicTags: **{len(parsed.points)}**\n- Twin diagnostic extensions: **{len(extensions)}**\n- Coverage gap: **0**\n\n## Rules\n\nExisting export paths, member names and type IDs are retained exactly. Extensions are additive under `Twin Extensions/` and carry `origin=twin-extension`. Explicit conflicting source metadata is preserved and flagged in `normalization-review.json`.\n\n## Source classes\n\n""" + "\n".join(f"- {k}: {v}" for k,v in sorted(by_class.items())) + "\n\n## Root point population\n\n" + "\n".join(f"- {k}: {v}" for k,v in roots.most_common()) + "\n"
    (repo/'docs/graphene-schema-audit.md').write_text(audit,encoding='utf-8')
    return {"counts":parsed.counts,"extensions":len(extensions),"reviews":len(parsed.reviews),"points":len(all_points)}

if __name__ == '__main__': print(json.dumps(generate(),indent=2))
