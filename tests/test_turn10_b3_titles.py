"""Turn 10 B.3: bare-title attachment (the half that shipped).

docs/turn10_decisions/a4_bare_titles.md s5a (`gram.title_adjacent_line`), s5b
(`gram.title_above_label`), acceptance set s5d.

The pass is switched by `tree_contract._TITLE_ATTACH_ON`, and the "before" side of every
assertion here is that constant monkeypatched off.  s5c (the SECTION_RE `$` separator) was
measured and not shipped -- a4_bare_titles.md s5f.
"""

import os
from pathlib import Path

import pytest

from edgar_itemize import tree_contract
from edgar_itemize.agenda import assign_paths
from edgar_itemize.blocks import Block, caps_ratio
from edgar_itemize.candidates import find_candidates
from edgar_itemize.grammar.contract import ContractGrammar
from edgar_itemize.toc import TocRegion
from edgar_itemize.tree_contract import build_contract_tree

G = ContractGrammar()
# The private contracts-text corpus (`--kind text`; not published): files at
# <cik6>/<cik>/<accession>_<seq>.txt under EDGAR_ITEMIZE_TEXT_CORPUS.
_CORPUS_ENV = os.environ.get("EDGAR_ITEMIZE_TEXT_CORPUS")
CORPUS = Path(_CORPUS_ENV) if _CORPUS_ENV else None

def _blocks(specs):
    """Build a text-era block stream with real offsets: one blank line between blocks."""
    out, pos = [], 0
    for i, (text, flags) in enumerate(specs):
        lines = text.split("\n")
        starts, ends, o = [], [], pos
        for ln in lines:
            starts.append(o)
            ends.append(o + len(ln))
            o += len(ln) + 1
        out.append(Block(idx=i, text=text, raw_start=pos, raw_end=pos + len(text), norm_start=pos,
                         norm_end=pos + len(text), lines=tuple(lines), caps_ratio=caps_ratio(text),
                         line_raw_starts=tuple(starts), line_raw_ends=tuple(ends), **flags))
        pos += len(text) + 2
    return out, pos


# One synthetic contract holding every case of s5a/s5b: a title below a label, a title
# above a label, and the four disqualifiers (a table, a terminal period, a line that is
# itself a candidate, a TOC region).
SPECS = [
    ("ARTICLE I", {}),                                                                    # 0 -> 5a
    ("DEFINITIONS", {}),                                                                  # 1
    ("Section 1.01. Defined Terms. As used in this Agreement the following apply.", {}),   # 2
    ("THE CREDITS", {}),                                                                  # 3 -> 5b
    ("ARTICLE II", {}),                                                                   # 4
    ("Section 2.01. Commitments. Each Lender severally agrees to make loans.", {}),        # 5
    ("ARTICLE III", {}),                                                                  # 6 in_table below
    ("LETTERS OF CREDIT", dict(in_table=True, table_id=1)),                                # 7
    ("Section 3.01. L/C Commitment. Each Issuing Lender agrees to issue.", {}),            # 8
    ("ARTICLE IV", {}),                                                                   # 9 terminal period below
    ("Representations and warranties.", {}),                                              # 10
    ("Section 4.01. Existence. Each Loan Party is duly organized.", {}),                   # 11
    ("ARTICLE V", {}),                                                                    # 12 candidate line below
    ("(a) Payment Obligations", {}),                                                      # 13
    ("Section 5.01. Events of Default. If any Loan Party fails to pay.", {}),              # 14
    ("ARTICLE VI", {}),                                                                   # 15 title line inside a TOC region
    ("MISCELLANEOUS", {}),                                                                # 16
    ("Section 6.01. Notices. All notices shall be in writing.", {}),                       # 17
]
TOC_BLOCK = 16


def _build():
    blocks, end = _blocks(SPECS)
    cands = find_candidates(blocks, G, era="text")
    # a TOC region over block 16 only, with an empty candidate range: no candidate is
    # condemned, so this isolates condition 8 (the title line sits inside a TOC region)
    toc = [TocRegion(first_cand=0, last_cand=-1, block_start=TOC_BLOCK, block_end=TOC_BLOCK, reason="toc.test")]
    nodes, _rej = build_contract_tree(blocks, cands, toc, G, doc_raw_start=0, doc_raw_end=end, norm_len=end)
    return blocks, nodes, end


def _arts(nodes):
    return {n.label_canon: n for n in nodes if n.level_kind == "article"}


def test_title_on_the_line_below_attaches():
    blocks, nodes, _ = _build()
    a = _arts(nodes)["ARTICLE 1"]
    assert a.title == "DEFINITIONS"
    assert "gram.title_adjacent_line" in a.rule_ids
    # head_raw_end extends to the end of the title line; nothing else moves
    assert a.head_raw_end == blocks[1].line_raw_ends[0]
    assert a.head_raw_start == blocks[0].raw_start
    assert a.raw_start == blocks[0].raw_start


def test_title_on_the_line_above_moves_raw_start_only():
    blocks, nodes, _ = _build()
    a = _arts(nodes)["ARTICLE 2"]
    assert a.title == "THE CREDITS"
    assert "gram.title_above_label" in a.rule_ids
    assert a.raw_start == blocks[3].raw_start and a.norm_start == blocks[3].norm_start
    # the head stays on the label -- every judge bank is keyed on head_raw_start
    assert a.head_raw_start == blocks[4].raw_start
    assert a.head_raw_end == blocks[4].line_raw_ends[0]


@pytest.mark.parametrize("label,why", [
    ("ARTICLE 3", "the line below is in a table"),
    ("ARTICLE 4", "the line below has a terminal period"),
    ("ARTICLE 5", "the line below is itself a candidate"),
    ("ARTICLE 6", "the line below is inside a TOC region"),
])
def test_disqualifiers_attach_nothing(label, why):
    _blocks_, nodes, _ = _build()
    a = _arts(nodes)[label]
    assert not (a.title or "").strip(), why
    assert not [r for r in a.rule_ids if r.startswith("gram.title_")], why


def test_paths_and_head_offsets_are_byte_identical(monkeypatch):
    """s5a/s5b create and delete no node: ordinal paths and every head_raw_start must be
    the same with the pass on and off (gate G3)."""
    monkeypatch.setattr(tree_contract, "_TITLE_ATTACH_ON", False)
    b0, n0, end0 = _build()
    p0 = assign_paths(n0, b0, doc_start=0, doc_end=end0)
    monkeypatch.setattr(tree_contract, "_TITLE_ATTACH_ON", True)
    b1, n1, end1 = _build()
    p1 = assign_paths(n1, b1, doc_start=0, doc_end=end1)

    assert [(n.node_id, n.parent_id, n.depth, n.level_kind, n.label_canon, n.head_raw_start, n.head_raw_end)
            for n in n0] != [(n.node_id, n.parent_id, n.depth, n.level_kind, n.label_canon, n.head_raw_start, n.head_raw_end)
                             for n in n1], "the pass must do something on this fixture"
    assert [n.head_raw_start for n in n0] == [n.head_raw_start for n in n1]
    assert {k: v for k, v in p0.items() if isinstance(k, int)} == {k: v for k, v in p1.items() if isinstance(k, int)}
    assert sum(1 for n in n1 if "gram.title_adjacent_line" in n.rule_ids) == 1
    assert sum(1 for n in n1 if "gram.title_above_label" in n.rule_ids) == 1


# --- s5d: the three named documents ------------------------------------------------------

def _parse(rel: str, acc: str, seq: int):
    from edgar_itemize.pipeline import parse_document
    from edgar_itemize.sgml import load_text_submission

    sub = load_text_submission(CORPUS / rel, acc, seq)
    return parse_document(sub, sub.documents[0], "0")


needs_corpus = pytest.mark.skipif(CORPUS is None or not CORPUS.exists(),
                                  reason="EDGAR_ITEMIZE_TEXT_CORPUS not set or not mounted "
                                         "(private contracts-text corpus)")


@needs_corpus
def test_named_doc_bank_form_attaches_nothing():
    """0000897101-97-001260_2.txt L291 `FINANCIAL COVENANTS`: a bank-form agreement with no
    label anywhere near the bare title -- nothing may be created or attached (s5d)."""
    r = _parse("000021/216983/0000897101-97-001260_2.txt", "0000897101-97-001260", 2)
    assert [n.level_kind for n in r.nodes] == ["document"]
    assert not [n for n in r.nodes if any(x.startswith("gram.title_") for x in n.rule_ids)]


@needs_corpus
def test_named_doc_backslash_label_still_has_no_candidate():
    """0001193125-13-280716_2.txt L1551 is `7\\. FINANCIAL COVENANTS.` -- the filer's
    backslash is a tokenisation gap, out of B.3's scope: no candidate, no attachment."""
    r = _parse("000113/1137091/0001193125-13-280716_2.txt", "0001193125-13-280716", 2)
    text = r.normalized_text
    assert "7\\. FINANCIAL COVENANTS." in text
    off = [b for b in r.blocks if "7\\. FINANCIAL COVENANTS." in b.text]
    assert off, "the line survives normalisation"
    idx = {b.idx for b in off}
    assert not [c for c in r.candidates if c.block_idx in idx]
    assert not [n for n in r.nodes if any(x.startswith("gram.title_") for x in n.rule_ids)
                and n.head_raw_start in {b.raw_start for b in off}]


@needs_corpus
def test_named_doc_article_titles_attach():
    """0001193125-11-343620_2.txt: ARTICLE III / blank / `LETTERS OF CREDIT` / blank /
    SECTION 3.01 -- the `title_of_prev` class s5a is for."""
    r = _parse("000104/1047122/0001193125-11-343620_2.txt", "0001193125-11-343620", 2)
    arts = {n.label_canon: n for n in r.nodes if n.level_kind == "article"}
    a3 = arts["ARTICLE 3"]
    assert a3.title == "LETTERS OF CREDIT" and "gram.title_adjacent_line" in a3.rule_ids
    assert a3.head_raw_end > a3.head_raw_start and a3.raw_start == a3.head_raw_start
    assert arts["ARTICLE 1"].title == "DEFINITIONS"
    assert sum(1 for n in r.nodes if "gram.title_adjacent_line" in n.rule_ids) == 10
