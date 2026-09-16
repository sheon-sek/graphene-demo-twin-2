import json
from datetime import datetime, timezone
from graphene_demo_twin.config import ROOT
from graphene_demo_twin.domain.model import DomainModel
from graphene_demo_twin.faults.engine import FaultEngine, FaultActivation


def fixture():
    m=json.loads((ROOT/'config/generated/graphene-coverage-manifest.json').read_text()); t=json.loads((ROOT/'config/generated/graphene-instance-topology.json').read_text()); model=DomainModel(m,t); snap=model.calculate('2026-08-28T02:30:00Z'); return m,snap


def find(m, contains, member):
    for p in m['points']:
        if contains.lower() in p['exportPath'].lower() and p['memberName'].lower()==member.lower(): return p
    raise AssertionError((contains,member))


def test_crac_valve_fault_correlated_symptoms():
    m,s=fixture(); target='CRAC/L1_CRAC1'
    # Actual instance names vary; select first CRAC and use extension path.
    p=next(p for p in m['points'] if p['typeId']=='CRAC' and p['memberName']=='CHW Valve Feedback'); target=p['instancePath']
    eff=FaultEngine().apply(s,m,[FaultActivation('x','CRAC_VALVE_STUCK',target,1.0,s.timestamp.isoformat())])
    feedback=next(q for q in m['points'] if q['instancePath']==target and q['memberName']=='CHW Valve Feedback')
    flow=next(q for q in m['points'] if q['instancePath']==target and q['memberName']=='CHW Flow')
    assert abs(eff['values'][feedback['signalKey']]-3)<0.01
    assert abs(eff['values'][flow['signalKey']]-0.3)<0.01


def test_network_failure_changes_quality_and_state():
    m,s=fixture(); p=next(p for p in m['points'] if p['typeId']=='Network Device' and p['memberName']=='Ping Time'); target=p['instancePath']
    eff=FaultEngine().apply(s,m,[FaultActivation('x','NETWORK_DEVICE_FAILURE',target,1.0,s.timestamp.isoformat())])
    assert eff['values'][p['signalKey']]==9999.0
    assert eff['quality'][p['signalKey']]=='Bad_CommunicationError'
