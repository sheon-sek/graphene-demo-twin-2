import json
from graphene_demo_twin.config import ROOT
from graphene_demo_twin.domain.model import DomainModel, parse_utc
from graphene_demo_twin.runtime.engine import RuntimeEngine


def load_model():
    m=json.loads((ROOT/'config/generated/graphene-coverage-manifest.json').read_text())
    t=json.loads((ROOT/'config/generated/graphene-instance-topology.json').read_text())
    return m,DomainModel(m,t)

def test_meter_pqs_pf_consistency():
    m,model=load_model(); snap=model.calculate('2026-08-28T06:00:00Z')
    inst=next(p['instancePath'] for p in m['points'] if p['typeId']=='GPM96' and p['memberName']=='P1')
    pts={p['memberName']:p for p in m['points'] if p['instancePath']==inst}
    for phase in ('1','2','3'):
        if all(k+phase in pts for k in ('P','Q','S','PF')):
            p=snap.signals[pts['P'+phase]['signalKey']]; q=snap.signals[pts['Q'+phase]['signalKey']]; ss=snap.signals[pts['S'+phase]['signalKey']]; pf=snap.signals[pts['PF'+phase]['signalKey']]
            assert abs(ss*ss-(p*p+q*q))/max(1,ss*ss) < .002
            assert abs(p/ss-pf) < .002

def test_chiller_output_power_cop_invariant():
    m,model=load_model(); snap=model.calculate('2026-08-28T06:00:00Z')
    inst=next(p['instancePath'] for p in m['points'] if p['typeId']=='Chiller' and p['memberName']=='COP')
    pts={p['memberName']:p for p in m['points'] if p['instancePath']==inst or (p['origin']=='twin-extension' and p['instancePath']==inst)}
    power=snap.signals[pts['Input Power']['signalKey']]; cop=snap.signals[pts['COP']['signalKey']]; out=snap.signals[pts['Cooling Output']['signalKey']]
    assert abs(out-power*cop) < .1

def test_energy_is_monotonic_random_access():
    m,model=load_model(); p=next(p for p in m['points'] if p['sourceClass']=='ENERGY_INTEGRAL' and p['runtimeRequired'])
    a=model.calculate('2026-08-28T00:00:00Z').signals[p['signalKey']]
    b=model.calculate('2026-08-29T00:00:00Z').signals[p['signalKey']]
    again=model.calculate('2026-08-28T00:00:00Z').signals[p['signalKey']]
    assert b>a>=0 and a==again

def test_demo_holds_but_open_world_does_not():
    r=RuntimeEngine(); r.set_mode('demo'); r.seek('2026-08-29T00:00:00Z'); r.state='RUNNING'; r._sim_anchor=parse_utc('2026-08-29T00:00:00Z')
    assert r.now() <= parse_utc(r.cfg['demoEndUtc']); assert r.state=='HOLDING'
    r.set_mode('open_world'); r.seek('2026-09-30T00:00:00Z'); r.state='RUNNING'; r._sim_anchor=parse_utc('2026-09-30T00:00:00Z')
    _=r.now(); assert r.state=='RUNNING'
