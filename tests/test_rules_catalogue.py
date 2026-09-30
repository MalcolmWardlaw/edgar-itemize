"""docs/RULES.md is generated from the source by scripts/rules_catalogue.py and must be current.

The catalogue is the documentation of the `rule_ids` vocabulary (docs/OUTPUT_CONTRACT.md
section 8.2) and of the closed `rejected.reason` vocabulary (section 7.6). A rule id added,
retired or moved without regenerating the file fails here; so does a rejected reason the
contract does not list.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rules_catalogue.py"

_spec = importlib.util.spec_from_file_location("rules_catalogue", SCRIPT)
rules_catalogue = importlib.util.module_from_spec(_spec)
sys.modules["rules_catalogue"] = rules_catalogue  # dataclasses resolve annotations through sys.modules
_spec.loader.exec_module(rules_catalogue)  # type: ignore[union-attr]


def test_rules_md_is_current():
    if not rules_catalogue._doc_sources():
        pytest.skip("provenance documents (turn plans, reports, decision memos) not present: private notebook only")
    entries, reasons = rules_catalogue.scan()
    rendered = rules_catalogue.render(entries, reasons)
    committed = rules_catalogue.OUT.read_text(encoding="utf-8")
    assert committed == rendered, "docs/RULES.md is stale: run `uv run python scripts/rules_catalogue.py`"


def test_every_rule_id_family_is_known():
    entries, _ = rules_catalogue.scan()
    unknown = {e.family for e in entries.values()} - set(rules_catalogue.FAMILIES)
    assert not unknown, f"rule id families without a description in FAMILIES: {sorted(unknown)}"


def test_rejected_reasons_match_the_output_contract():
    _, reasons = rules_catalogue.scan()
    contract = (ROOT / "docs" / "OUTPUT_CONTRACT.md").read_text(encoding="utf-8")
    section = contract.split("### 7.6", 1)[1].split("### 7.7", 1)[0]
    listed = set(re.findall(r"^\| `([a-z_.]+)` \|", section, flags=re.M))
    assert set(reasons) == listed, f"code writes {sorted(set(reasons) - listed)} the contract does not list; " \
                                   f"contract lists {sorted(listed - set(reasons))} the code never writes"


def test_ids_the_contract_names_exist():
    entries, _ = rules_catalogue.scan()
    contract = (ROOT / "docs" / "OUTPUT_CONTRACT.md").read_text(encoding="utf-8")
    section = contract.split("### 8.2", 1)[1].split("### 8.3", 1)[0]
    named = set(re.findall(r"`((?:gram|sty|toc|rej|seq|chain|seg|agenda|tree|ocr)\.[a-z_.]+)`", section))
    known = set(entries)
    missing = {n for n in named if n not in known and not any(k.endswith("*") and n.startswith(k[:-1]) for k in known)}
    assert not missing, f"section 8.2 names rule ids the source no longer carries: {sorted(missing)}"
