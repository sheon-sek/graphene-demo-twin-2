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
    run=sub.add_parser('serve'); run.add_argument('--host',default='127.0.0.1'); run.add_argument('--port',type=int,default=8080); run.add_argument('--opc-endpoint',default='opc.tcp://127.0.0.1:4840/graphene/twin'); run.add_argument('--no-opc',action='store_true',help='disable the in-process OPC UA server')
    status=sub.add_parser('status'); status.add_argument('--url',default='http://127.0.0.1:8080')
    rc=sub.add_parser('runtime'); rc.add_argument('action',choices=['start','stop','pause','resume','reset']); rc.add_argument('--url',default='http://127.0.0.1:8080')
    scale=sub.add_parser('time-scale'); scale.add_argument('value',type=float); scale.add_argument('--url',default='http://127.0.0.1:8080')
    a=p.parse_args()
    if a.cmd=='generate-schema': print(json.dumps(generate(),indent=2)); return
    if a.cmd=='serve':
        import threading, uvicorn
        from graphene_demo_twin.admin import app as admin_app
        generate()
        engine=admin_app.rt()
        if not a.no_opc:
            def _opc():
                from graphene_demo_twin.adapters.opcua import serve as opc_serve
                asyncio.run(opc_serve(engine,a.opc_endpoint))
            threading.Thread(target=_opc,name='opcua',daemon=True).start()
        uvicorn.run(admin_app.app,host=a.host,port=a.port,log_level='info'); return
    if a.cmd=='status': print(json.dumps(http_json(a.url+'/api/status'),indent=2)); return
    command={'commandId':str(uuid.uuid4()),'confirm':True}
    if a.cmd=='runtime': print(json.dumps(http_json(a.url+f'/api/runtime/{a.action}','POST',command),indent=2)); return
    if a.cmd=='time-scale': command['value']=a.value; print(json.dumps(http_json(a.url+'/api/runtime/time-scale','POST',command),indent=2)); return
if __name__=='__main__': main()
