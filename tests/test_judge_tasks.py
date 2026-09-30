"""Judge tooling (evaluation only): task registry, window hygiene, status matching."""

from edgar_itemize.blocks import Block
from edgar_itemize.eval.judge import block_at, block_near, current_status, key, mark_window
from edgar_itemize.llm import TASKS, clean_window, subheading_prompt
from edgar_itemize.tree import Node, Rejected


class _R:
    def __init__(self, nodes, rejected=()):
        self.nodes = nodes; self.rejected = list(rejected)


def _node(kind, start, depth=3, label=None):
    return Node(1, 0, depth, kind, label, label, "t", start, start + 100, start, start + 10, 0, 0, 0, 0.6, [])


def test_clean_window_strips_furniture_and_glue():
    w = "PART I\nTable of Contents\n>>> ITEM 1. BUSINESS Table of Contents\nGeneral\nBack to top\nprose"
    assert clean_window(w) == "PART I\n>>> ITEM 1. BUSINESS\nGeneral\nprose"
    # a marked line that is nothing but the glue is left alone (nothing to judge otherwise)
    assert clean_window(">>> Table of Contents") == ">>> Table of Contents"


def test_task_registry_builds_prompts():
    for name, (schema, system, build, flat) in TASKS.items():
        w = dict(task=name, label="ITEM 1", window="x\n>>> y\nz", context="ITEM 7", proposed="Net Sales.")
        assert build(w).endswith("x\n>>> y\nz"), name
        assert "required" in schema and system
    assert "Net Sales." in subheading_prompt("ITEM 7", "w", "Net Sales.")


def test_boundary_binary_task_schema_and_flatten():
    schema, system, build, flat = TASKS["boundary_binary"]
    assert schema["required"] == ["new_instrument", "kind", "reason"]
    assert schema["properties"]["new_instrument"]["type"] == "boolean"
    assert "none" in schema["properties"]["kind"]["enum"] and "agreement" in schema["properties"]["kind"]["enum"]
    assert "instrument" in system.lower()
    prompt = build(dict(window="prior text\n>>> ARTICLE 1\nDefinitions"))
    assert prompt.endswith("prior text\n>>> ARTICLE 1\nDefinitions")
    out = flat(dict(new_instrument=True, kind="agreement", reason="own title and recitals"))
    assert out == dict(is_heading=None, new_instrument=True, llm_kind="agreement", reason="own title and recitals")


def test_key_defaults_to_heading_task():
    assert key(dict(accession="a", raw_start=5, label="ITEM 1")) == ("heading", "a", 0, 5, "ITEM 1")
    assert key(dict(task="subheading", accession="a", sequence=2, raw_start=5, label="x"))[0] == "subheading"


def test_current_status_heading_and_subheading():
    r = _R([_node("item", 100, 2, "ITEM 1"), _node("heading", 250)], [Rejected(3, "item", "ITEM 2", 0.3, "nonmonotone", 400, "ITEM 2")])
    assert current_status(r, dict(raw_start=100, label="ITEM 1")) == "accepted"
    assert current_status(r, dict(raw_start=400, label="ITEM 2")) == "nonmonotone"
    assert current_status(r, dict(raw_start=999, label="ITEM 3")) == "no_candidate"
    # a run-in window is matched by block range, so a future node anywhere in the block counts
    sub = dict(task="subheading", raw_start=260, label="Net Sales.", block_raw_start=240, block_raw_end=400)
    assert current_status(r, sub) == "accepted"
    assert current_status(r, dict(sub, block_raw_start=300)) == "no_candidate"


def test_block_at_and_mark_window():
    blocks = [Block(0, "a", 0, 10, 0, 1), Block(1, "b", 10, 30, 2, 3), Block(2, "c", 30, 40, 4, 5)]
    assert block_at(blocks, 15).idx == 1
    assert block_at(blocks, 30).idx == 2
    assert block_at(blocks, 45) is None
    assert mark_window("0123456789", 4, 6, 2) == "23\n>>> 45\n67"


def test_block_near_accepts_tag_offset_before_block():
    blocks = [Block(0, "a", 0, 10, 0, 1), Block(1, "Tax Reform. On Dec", 100, 300, 2, 3)]
    assert block_near(blocks, 15, lead="Tax Reform.").idx == 1  # <b> tag 85 bytes before the text
    assert block_near(blocks, 15, lead="Other.") is None  # next block does not start with the lead
    assert block_near(blocks, 15, ahead=50) is None
