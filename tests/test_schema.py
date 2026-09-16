import json
from pathlib import Path
from graphene_demo_twin.config import ROOT
from graphene_demo_twin.schema.parser import inventory_counts, parse_exports
from graphene_demo_twin.schema.generate import generate


def test_reference_inventory_and_zero_coverage_gap():
    result=generate(ROOT)
    assert result['counts']=={'udtTypes':47,'udtInstances':831,'folders':296,'atomicTags':8741}
    manifest=json.loads((ROOT/'config/generated/graphene-coverage-manifest.json').read_text())
    exported=[p for p in manifest['points'] if p['origin']!='twin-extension']
    raw=inventory_counts(ROOT/'reference/graphene/real-graphene-demo-tag-instances.json')
    assert raw['AtomicTag']==len(exported)==8741
    assert len({p['exportPath'] for p in exported})==8741
    assert not any(p['sourceClass'] in {'UNMAPPED','UNKNOWN_AND_IGNORED'} for p in manifest['points'])


def test_extension_never_shadows_export():
    manifest=json.loads((ROOT/'config/generated/graphene-coverage-manifest.json').read_text())
    base={p['exportPath'] for p in manifest['points'] if p['origin']!='twin-extension'}
    ext={p['exportPath'] for p in manifest['points'] if p['origin']=='twin-extension'}
    assert base.isdisjoint(ext)
