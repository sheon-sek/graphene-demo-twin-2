from __future__ import annotations

from datetime import datetime
from typing import Any

class GrapheneProjector:
    def __init__(self, manifest:dict): self.manifest=manifest
    def project(self, snapshot, effective:dict|None=None, overrides:dict|None=None)->dict[str,dict[str,Any]]:
        eff=effective or {'values':snapshot.signals,'quality':{}}
        overrides=overrides or {}
        out={}
        for p in self.manifest['points']:
            key=p['signalKey']; base=snapshot.signals.get(key); effective_value=eff['values'].get(key,base)
            published=overrides.get(p['exportPath'],effective_value)
            source='RAW_OVERRIDE' if p['exportPath'] in overrides else ('INJECTED' if effective_value != base else 'BASE')
            out[p['exportPath']]={
                'exportPath':p['exportPath'],'value':published,'baseValue':base,'effectiveValue':effective_value,
                'publishedValue':published,'dataType':p['dataType'],'engUnit':p.get('engUnit'),'quality':eff.get('quality',{}).get(key,'Good'),
                'sourceTimestamp':snapshot.timestamp.isoformat(),'instanceName':p.get('instanceName'),'typeId':p.get('typeId'),
                'memberName':p['memberName'],'signalKey':key,'role':p['sourceClass'],'origin':p['origin'],'scope':p['scope'],'aiVisible':p['aiVisible'],'valueSource':source,
            }
        return out
