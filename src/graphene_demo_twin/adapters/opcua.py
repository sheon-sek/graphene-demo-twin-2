from __future__ import annotations

import asyncio
from graphene_demo_twin.runtime.engine import RuntimeEngine

NAMESPACE='urn:eetarp:graphene:demo:twin'

async def serve(runtime:RuntimeEngine, endpoint='opc.tcp://127.0.0.1:4840/graphene/twin'):
    try:
        from asyncua import Server, ua
    except ImportError as e:
        raise RuntimeError('Install project dependencies with: uv sync') from e
    server=Server(); await server.init(); server.set_endpoint(endpoint); idx=await server.register_namespace(NAMESPACE)
    folders={'':server.nodes.objects}
    variables=[]
    snapshot=runtime.snapshot()['points']
    for path,point in snapshot.items():
        parts=path.split('/'); parent=''
        for segment in parts[:-1]:
            key=f'{parent}/{segment}'.strip('/')
            if key not in folders: folders[key]=await folders[parent].add_folder(idx,segment)
            parent=key
        nodeid=ua.NodeId(f'point:{path}',idx); var=await folders[parent].add_variable(nodeid,parts[-1],point['value'])
        variables.append((path,var))
    await server.start()
    try:
        while True:
            points=runtime.snapshot()['points']
            for path,var in variables:
                p=points[path]; dv=ua.DataValue(ua.Variant(p['value'])); dv.SourceTimestamp=runtime.now(); await var.write_value(dv)
            await asyncio.sleep(1)
    finally: await server.stop()
