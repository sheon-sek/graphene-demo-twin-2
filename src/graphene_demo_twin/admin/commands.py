from __future__ import annotations

from collections import OrderedDict
from typing import Any, Callable

from graphene_demo_twin.runtime.engine import RuntimeEngine

class AdminCommandService:
    """Single mutation boundary for HTTP/CLI clients.

    `commandId` is idempotent within a bounded in-memory window. The simulation runtime remains
    authoritative; this service only validates, deduplicates and records command outcomes.
    """
    def __init__(self, runtime: RuntimeEngine, max_results: int = 1000):
        self.runtime=runtime; self.max_results=max_results; self._results: OrderedDict[str,dict[str,Any]]=OrderedDict()

    def execute(self, command_id:str, confirm:bool, action:str, fn:Callable[[],Any]) -> dict[str,Any]:
        if not command_id.strip(): raise ValueError('commandId is required')
        if not confirm: raise ValueError('confirm must be true')
        if command_id in self._results:
            cached=dict(self._results[command_id]); cached['idempotentReplay']=True; return cached
        payload=fn()
        result={'ok':True,'commandId':command_id,'action':action,'idempotentReplay':False,'status':self.runtime.status()}
        if payload is not None: result['result']=payload
        self.runtime._event('ADMIN_COMMAND',commandId=command_id,action=action)
        self._results[command_id]=result
        while len(self._results)>self.max_results:self._results.popitem(last=False)
        return result
