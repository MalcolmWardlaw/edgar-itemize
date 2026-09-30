"""Metric credit for multi-item headings (cases A4/D3) and omitted_ok plumbing."""

from edgar_itemize.eval.metrics import doc_metrics, doc_metrics_10q
from edgar_itemize.pipeline import covers_items_of, parse_document, result_rows
from edgar_itemize.sgml import load_submission, split_documents, Submission, header_text

DOC = dict(error=None, doc_raw_start=0, doc_raw_end=100_000, filed_year=1994, signals="{}", toc_found=False)


def _node(canon, start, end, rule_ids=(), kind="item"):
    return dict(level_kind=kind, label_canon=canon, raw_start=start, raw_end=end, rule_ids=list(rule_ids))


def _core_nodes(skip=(), extra_rules=None):
    """ITEM 1..8 core chain, optionally skipping some and tagging others."""
    labels = ["ITEM 1", "ITEM 2", "ITEM 3", "ITEM 5", "ITEM 7", "ITEM 8"]
    nodes, pos = [], 1000
    for lab in labels:
        if lab in skip:
            continue
        rules = ["lbl.item"] + list((extra_rules or {}).get(lab, []))
        nodes.append(_node(lab, pos, pos + 10_000, rules))
        pos += 10_000
    return nodes


def test_core_complete_baseline():
    m = doc_metrics(DOC, _core_nodes())
    assert m["core_complete"] and not m["missing_core"]


def test_multi_item_credit_covers_missing_core():
    # "Items 1 and 2. Business and Properties": one accepted node labeled ITEM 1
    # carrying multi.ITEM 2 must credit Item 2 (IBP 0000052477-94-000003 shape).
    nodes = _core_nodes(skip=("ITEM 2",), extra_rules={"ITEM 1": ["lbl.item.multi", "multi.ITEM 2"]})
    m = doc_metrics(DOC, nodes)
    assert m["core_complete"] and m["missing_core"] == []


def test_no_credit_without_multi_tag():
    nodes = _core_nodes(skip=("ITEM 2",))
    m = doc_metrics(DOC, nodes)
    assert not m["core_complete"] and m["missing_core"] == ["2"]


def test_multi_item_range_credit():
    # "Items 10 through 13" style: multi.ITEM 11/12/13 on the ITEM 10 node
    nodes = _core_nodes() + [_node("ITEM 10", 90_000, 95_000, ["lbl.item", "lbl.item.multi", "multi.ITEM 11", "multi.ITEM 12", "multi.ITEM 13"])]
    m = doc_metrics(DOC, nodes)
    assert not {"11", "12", "13"} & set(m["missing_expected"])


def test_omitted_ok_default_none():
    m = doc_metrics(DOC, _core_nodes(skip=("ITEM 7", "ITEM 8")))
    assert not m["core_complete"] and m["omitted_ok"] is False


def test_omitted_ok_signal_covers_all_missing():
    m = doc_metrics(DOC, _core_nodes(skip=("ITEM 7", "ITEM 8")), omitted_items={"7", "8"})
    assert not m["core_complete"]  # raw metric unchanged
    assert m["omitted_ok"] is True


def test_omitted_ok_partial_coverage_is_false():
    m = doc_metrics(DOC, _core_nodes(skip=("ITEM 7", "ITEM 8")), omitted_items={"7"})
    assert m["omitted_ok"] is False


def test_omit_stmt_nodes_feed_signal_not_items_found():
    # Turn-4 recognizer emits omit.stmt item nodes; they are an omission signal,
    # never a found item.
    nodes = _core_nodes(skip=("ITEM 7", "ITEM 8"))
    nodes += [_node("ITEM 7", 500, 500, ["omit.stmt"]), _node("ITEM 8", 500, 500, ["omit.stmt"])]
    m = doc_metrics(DOC, nodes)
    assert not m["core_complete"] and m["missing_core"] == ["7", "8"]
    assert m["omitted_ok"] is True
    assert m["n_items"] == 4  # omit.stmt nodes not counted


def test_omitted_ok_false_when_complete():
    m = doc_metrics(DOC, _core_nodes(), omitted_items={"7"})
    assert m["core_complete"] and m["omitted_ok"] is False


# --- end-to-end: items_found credit + IBR columns through parse_document ---

SUB = (
    "<SEC-HEADER>\nACCESSION NUMBER: 0000000001-96-000001\n"
    "COMPANY CONFORMED NAME:\t\tTEST PACKING CO\n</SEC-HEADER>\n"
    "<DOCUMENT>\n<TYPE>10-K\n<SEQUENCE>1\n<TEXT>\n\n<PAGE>\n"
    "                                PART I\n\n"
    "ITEMS 1 AND 2. BUSINESS AND PROPERTIES\n\nThe company packs things. " + "Plants are owned. " * 40 + "\n\n"
    "ITEM 3. LEGAL PROCEEDINGS\n\nNone.\n\n"
    "ITEM 4. SUBMISSION OF MATTERS TO A VOTE OF SECURITY HOLDERS\n\nNone.\n\n"
    "                                PART II\n\n"
    "ITEM 5. MARKET FOR THE REGISTRANT'S COMMON STOCK\n\nListed on the NYSE.\n\n"
    "ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS\n\n"
    "Incorporated by reference to pages 10 to 20 of the 1996 Annual Report to Shareholders.\n\n"
    "ITEM 8. FINANCIAL STATEMENTS AND SUPPLEMENTARY DATA\n\n"
    "The consolidated financial statements on pages 23 to 33 of the 1996 Annual Report\n"
    "to Shareholders are incorporated herein by reference.\n\n"
    "</TEXT>\n</DOCUMENT>\n"
    "<DOCUMENT>\n<TYPE>EX-13\n<SEQUENCE>2\n<DESCRIPTION>ANNUAL REPORT\n<TEXT>\nannual report pages\n</TEXT>\n</DOCUMENT>\n"
)


def _parse():
    from pathlib import Path

    sub = Submission(path=Path("0000000001-96-000001.txt"), text=SUB, header=header_text(SUB), documents=split_documents(SUB))
    return parse_document(sub, sub.documents[0], "12345")


def test_items_found_credits_multi_item():
    r = _parse()
    _nodes, doc, _rej = result_rows(r, 1996, keep_text=False)
    assert "ITEM 1" in doc["items_found"]
    assert "ITEM 2" in doc["items_found"]  # covered by multi.ITEM 2, no standalone heading
    assert doc["items_found"].count("ITEM 2") == 1


def test_ibr_columns_on_item_nodes():
    r = _parse()
    nodes, _doc, _rej = result_rows(r, 1996, keep_text=False)
    by = {n["label_canon"]: n for n in nodes if n["level_kind"] == "item"}
    n8 = by["ITEM 8"]
    assert n8["item_incorporated_by_reference"] is True
    assert n8["ibr_target"] == "annual_report"
    assert n8["ibr_target_in_submission"] is True  # EX-13 present in the submission
    n3 = by["ITEM 3"]
    assert n3["item_incorporated_by_reference"] is False
    assert n3["ibr_target"] is None and n3["ibr_target_in_submission"] is None


# --- Turn 7 (c): covers_items column ---------------------------------------


def test_covers_items_of_null_without_multi_tag():
    assert covers_items_of(["lbl.item"]) is None
    assert covers_items_of([]) is None
    assert covers_items_of(None) is None


def test_covers_items_of_single():
    assert covers_items_of(["lbl.item", "lbl.item.multi", "multi.ITEM 2"]) == ["2"]


def test_covers_items_of_range():
    assert covers_items_of(["lbl.item", "lbl.item.multi", "multi.ITEM 11", "multi.ITEM 12", "multi.ITEM 13"]) == ["11", "12", "13"]


def test_result_rows_populates_covers_items():
    # end-to-end: "Items 1 and 2" -> the ITEM 1 node carries covers_items == ["2"].
    r = _parse()
    nodes, _doc, _rej = result_rows(r, 1996, keep_text=False)
    by = {n["label_canon"]: n for n in nodes if n["level_kind"] == "item"}
    assert by["ITEM 1"]["covers_items"] == ["2"]
    assert by["ITEM 3"]["covers_items"] is None  # not a multi-item node


def test_doc_metrics_reads_covers_items_column():
    # New-format node dict: covers_items present (typed column), not just rule_ids.
    nodes = _core_nodes(skip=("ITEM 2",), extra_rules={"ITEM 1": ["lbl.item.multi", "multi.ITEM 2"]})
    for n in nodes:
        n["covers_items"] = covers_items_of(n["rule_ids"])
    m = doc_metrics(DOC, nodes)
    assert m["core_complete"] and m["missing_core"] == []


def test_doc_metrics_covers_items_and_rule_ids_fallback_agree():
    # Same underlying multi.ITEM data, read via covers_items or via the legacy
    # rule_ids fallback (no covers_items key at all) must give identical results.
    rules_nodes = _core_nodes(skip=("ITEM 10",)) + [
        _node("ITEM 10", 90_000, 95_000, ["lbl.item", "lbl.item.multi", "multi.ITEM 11", "multi.ITEM 12", "multi.ITEM 13"])
    ]
    typed_nodes = [dict(n, covers_items=covers_items_of(n["rule_ids"])) for n in rules_nodes]
    assert doc_metrics(DOC, rules_nodes) == doc_metrics(DOC, typed_nodes)


# --- Turn 7 (e): 10-Q three-tier expected items -----------------------------

DOC_10Q = dict(error=None, doc_raw_start=0, doc_raw_end=100_000, toc_found=True)


def _q_node(canon):
    return dict(level_kind="item", label_canon=canon, rule_ids=[])


def test_10q_required_missing_fails_core():
    # II.6 (Exhibits) missing: REQUIRED, so incomplete even though everything
    # else is present.
    nodes = [_q_node(c) for c in ("ITEM I.1", "ITEM I.2", "ITEM II.1", "ITEM II.2", "ITEM II.3", "ITEM II.4", "ITEM II.5")]
    m = doc_metrics_10q(dict(DOC_10Q, filed_year=1994), nodes)
    assert not m["core_complete"]
    assert m["missing_core"] == ["II.6"]


def test_10q_omittable_missing_is_complete_with_omissions():
    # Part II Items 1-5 all genuinely absent (a quiet quarter): REQUIRED items
    # present -> core_complete True, but flagged complete_with_omissions.
    nodes = [_q_node(c) for c in ("ITEM I.1", "ITEM I.2", "ITEM II.6")]
    m = doc_metrics_10q(dict(DOC_10Q, filed_year=1994), nodes)
    assert m["core_complete"]
    assert m["complete_with_omissions"] is True
    assert m["missing_core"] == []
    assert set(m["missing_expected"]) == {"II.1", "II.2", "II.3", "II.4", "II.5"}


def test_10q_fully_complete_no_omissions():
    nodes = [_q_node(c) for c in ("ITEM I.1", "ITEM I.2", "ITEM II.1", "ITEM II.2", "ITEM II.3", "ITEM II.4", "ITEM II.5", "ITEM II.6")]
    m = doc_metrics_10q(dict(DOC_10Q, filed_year=1994), nodes)
    assert m["core_complete"] and not m["complete_with_omissions"]
    assert m["missing_expected"] == []


def test_10q_era_required_not_yet_mandated_is_not_missing_core():
    # I.3 (Market Risk) not mandated until 1998: a 1994 filing without it is
    # not missing_core.
    nodes = [_q_node(c) for c in ("ITEM I.1", "ITEM I.2", "ITEM II.6")]
    m = doc_metrics_10q(dict(DOC_10Q, filed_year=1994), nodes)
    assert "I.3" not in m["missing_core"]
    m = doc_metrics_10q(dict(DOC_10Q, filed_year=1997), nodes)
    assert "I.3" not in m["missing_core"]


def test_10q_era_required_boundary_years():
    # Boundary is inclusive at the mandate year: I.3 required from 1998, I.4
    # from 2003, II.1A from 2006.
    nodes = [_q_node(c) for c in ("ITEM I.1", "ITEM I.2", "ITEM II.6")]  # I.3/I.4/II.1A all missing
    m_1997 = doc_metrics_10q(dict(DOC_10Q, filed_year=1997), nodes)
    m_1998 = doc_metrics_10q(dict(DOC_10Q, filed_year=1998), nodes)
    assert "I.3" not in m_1997["missing_core"] and "I.3" in m_1998["missing_core"]

    m_2002 = doc_metrics_10q(dict(DOC_10Q, filed_year=2002), nodes)
    m_2003 = doc_metrics_10q(dict(DOC_10Q, filed_year=2003), nodes)
    assert "I.4" not in m_2002["missing_core"] and "I.4" in m_2003["missing_core"]

    m_2005 = doc_metrics_10q(dict(DOC_10Q, filed_year=2005), nodes)
    m_2006 = doc_metrics_10q(dict(DOC_10Q, filed_year=2006), nodes)
    assert "II.1A" not in m_2005["missing_core"] and "II.1A" in m_2006["missing_core"]


def test_10q_omittable_items_never_era_gated():
    from edgar_itemize.grammar.form10q import Form10QGrammar

    g = Form10QGrammar()
    assert set(g.omittable_items(1994)) == set(g.omittable_items(2024)) == {"II.1", "II.2", "II.3", "II.4", "II.5"}
