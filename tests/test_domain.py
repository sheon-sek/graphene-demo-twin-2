import json
from graphene_demo_twin.config import ROOT
from graphene_demo_twin.domain.model import DomainModel, deterministic_noise


def model():
    m=json.loads((ROOT/'config/generated/graphene-coverage-manifest.json').read_text())
    t=json.loads((ROOT/'config/generated/graphene-instance-topology.json').read_text())
    return DomainModel(m,t)


def test_determinism_random_access():
    x=model(); times=['2026-08-28T03:00:00Z','2026-08-28T01:00:00Z','2026-09-20T00:00:00Z','2026-08-28T02:00:00Z','2026-08-28T01:00:00Z']
    snaps=[x.calculate(t,'demo') for t in times]
    assert snaps[1].site==snaps[4].site
    assert snaps[1].signals==snaps[4].signals


def test_all_runtime_points_have_typed_value():
    x=model(); snap=x.calculate('2026-08-28T02:00:00Z')
    for p in x.manifest['points']:
        if p['runtimeRequired']:
            assert p['signalKey'] in snap.signals
            assert snap.signals[p['signalKey']] is not None


def test_pre_epoch_rejected():
    import pytest
    with pytest.raises(ValueError): model().calculate('2026-01-01T00:00:00Z')
