from __future__ import annotations
import argparse, asyncio, json, uuid
from urllib.request import Request, urlopen
from graphene_demo_twin.schema.generate import generate

def http_json(url:str,method='GET',body=None):
    data=json.dumps(body).encode() if body is not None else None
    req=Request(url,data=data,method=method,headers={'content-type':'application/json'} if body is not None else {})
    with urlopen(req,timeout=10) as r:return json.load(r)

def main():
    p=argparse.ArgumentParser(prog='graphene-twin'); sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('generate-schema')
    run=sub.add_parser('serve'); run.add_argument('--host',default='127.0.0.1'); run.add_argument('--port',type=int,default=8080)
    opc=sub.add_parser('opc'); opc.add_argument('--endpoint',default='opc.tcp://127.0.0.1:4840/graphene/twin')
    status=sub.add_parser('status'); status.add_argument('--url',default='http://127.0.0.1:8080')
    rc=sub.add_parser('runtime'); rc.add_argument('action',choices=['start','stop','pause','resume','reset']); rc.add_argument('--url',default='http://127.0.0.1:8080')
    scale=sub.add_parser('time-scale'); scale.add_argument('value',type=float); scale.add_argument('--url',default='http://127.0.0.1:8080')
    a=p.parse_args()
    if a.cmd=='generate-schema': print(json.dumps(generate(),indent=2)); return
    if a.cmd=='serve':
        import uvicorn; generate(); uvicorn.run('graphene_demo_twin.admin.app:app',host=a.host,port=a.port,reload=False); return
    if a.cmd=='opc':
        generate(); from graphene_demo_twin.runtime.engine import RuntimeEngine; from graphene_demo_twin.adapters.opcua import serve; asyncio.run(serve(RuntimeEngine(),a.endpoint)); return
    if a.cmd=='status': print(json.dumps(http_json(a.url+'/api/status'),indent=2)); return
    command={'commandId':str(uuid.uuid4()),'confirm':True}
    if a.cmd=='runtime': print(json.dumps(http_json(a.url+f'/api/runtime/{a.action}','POST',command),indent=2)); return
    if a.cmd=='time-scale': command['value']=a.value; print(json.dumps(http_json(a.url+'/api/runtime/time-scale','POST',command),indent=2)); return
if __name__=='__main__': main()
