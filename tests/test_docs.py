"""The P5 documents stay true to the code (#28)."""

import ast
import re
from pathlib import Path

from graphene_demo_twin.faults import STANDARD_CATALOG
from graphene_demo_twin.faults.reference import render

ROOT = Path(__file__).resolve().parents[1]
TEST_REF = re.compile(r"`(tests/test_\w+\.py)::(test_\w+)`")


def test_the_fault_catalog_reference_is_generated_from_the_catalog(plant_design):
    checked_in = (ROOT / "docs" / "fault-catalog.md").read_text(encoding="utf-8")
    assert checked_in == render(STANDARD_CATALOG, plant_design), (
        "regenerate: python -m graphene_demo_twin.faults.reference > docs/fault-catalog.md"
    )


def test_every_v1_issue_maps_to_acceptance_tests_that_exist():
    doc = (ROOT / "docs" / "v1-acceptance.md").read_text(encoding="utf-8")
    rows = {int(m[1]): m[0] for m in re.finditer(r"^\| #(\d+) \|.*$", doc, flags=re.M)}
    assert sorted(rows) == list(range(2, 11))
    defined: dict[str, set[str]] = {}
    for issue, row in rows.items():
        refs = TEST_REF.findall(row)
        assert refs, f"#{issue} names no acceptance test"
        for module, name in refs:
            if module not in defined:
                tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
                defined[module] = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
            assert name in defined[module], f"#{issue}: {module}::{name} does not exist"
