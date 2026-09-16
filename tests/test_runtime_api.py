from fastapi.testclient import TestClient
import uuid
from graphene_demo_twin.admin.app import app

client=TestClient(app)

def command(path, extra=None):
    body={'commandId':str(uuid.uuid4()),'confirm':True}; body.update(extra or {})
    return client.post(path,json=body)

def test_api_status_and_commands():
    assert client.get('/api/health').json()['ok'] is True
    assert command('/api/runtime/start').status_code==200
    assert command('/api/runtime/pause').json()['status']['state']=='PAUSED'
    assert command('/api/runtime/resume').json()['status']['state']=='RUNNING'
    assert command('/api/runtime/time-scale',{'value':120}).json()['status']['timeScale']==120

def test_fault_inject_and_clear():
    r=command('/api/faults/inject',{'recipeId':'UPS_RECTIFIER_FAULT','targetAsset':'UPS/UPS 8','severity':1.0})
    assert r.status_code==200
    faults=client.get('/api/faults/active').json()['faults']; assert faults
    fid=faults[-1]['injectionId']
    assert client.delete(f'/api/faults/{fid}?commandId=clear&confirm=true').status_code==200

def test_coverage_endpoint():
    c=client.get('/api/schema/coverage').json(); assert c['exportedPointCount']==8741; assert c['totalPointCount']>=8741

def test_command_idempotency():
    body={'commandId':'same-command','confirm':True,'value':30}
    a=client.post('/api/runtime/time-scale',json=body).json(); b=client.post('/api/runtime/time-scale',json=body).json()
    assert a['idempotentReplay'] is False
    assert b['idempotentReplay'] is True

def test_raw_override_is_post_projection_and_reset_does_not_clear_it():
    snap=client.get('/api/snapshot?points=true').json()
    path=next(iter(snap['points']))
    original=snap['points'][path]['value']
    r=command('/api/overrides',{'exportPath':path,'value':12345})
    assert r.status_code==200
    changed=client.get('/api/snapshot?points=true').json()['points'][path]
    assert changed['publishedValue']==12345
    assert changed['effectiveValue']!=12345 or original==12345
    assert changed['valueSource']=='RAW_OVERRIDE'
    command('/api/runtime/reset')
    assert client.get('/api/overrides/active').json()['overrides'][path]==12345
    client.delete(f'/api/overrides/{path}?commandId={uuid.uuid4()}&confirm=true')
