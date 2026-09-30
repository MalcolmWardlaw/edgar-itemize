"""Turn 13 B.1b -- rej.regab_back_matter and the seq.regab_run node tag.

docs/turn13_decisions/b1_regab_build.md sections 6 and 9: the B.1 gate found 67 Reg-AB
nodes in 51 documents with no `gram.item.title_match` accepted at or after the back-matter
boundary -- an EX-35 exhibit's own "ITEM 1123 ANNUAL STATEMENT OF COMPLIANCE" heading
(35 `0001056404-07-*` documents; in 32 it displaced the body "Item 1123 of Regulation AB,
Servicer Compliance Statement.") and exhibit-index rows "Item 1122 Report on Assessment
..." / "Item 1123 Certification" (16 documents). A title-matched Reg-AB block the filer
prints after the exhibit index (11 documents) is real and stays.

The boundary is computed from the tree WITHOUT Reg-AB items and PART V
(`agenda.regab_back_matter`); the tree is rebuilt only when an untitled Reg-AB item was
accepted past it. These tests go through `pipeline.parse_document`, where the rebuild
lives.
"""

from edgar_itemize import pipeline
from edgar_itemize.grammar.form10k import Form10KGrammar
from edgar_itemize.pipeline import parse_document
from edgar_itemize.sgml import load_text_submission

G = Form10KGrammar()
FILL = "\n".join(f"Line {i} of the exhibit text, which runs on for a while to make the exhibit long." for i in range(400))

HEAD = """PART I

Item 1. Business.

Omitted.

Item 1A. Risk Factors.

Omitted.

Item 2. Properties.

Omitted.

Item 3. Legal Proceedings.

Omitted.

PART II

Item 5. Market for Registrant's Common Equity.

Omitted.

Item 9B. Other Information.

None.

PART III

Item 10. Directors and Executive Officers.

Omitted.

"""

INDEX = """EXHIBIT INDEX

Exhibit 4.1 Pooling and Servicing Agreement, incorporated by reference.

Exhibit 31 Rule 13a-14(d) Certification, filed herewith.

Exhibit 33 Reports on assessment of compliance with servicing criteria, filed herewith.

Exhibit 34 Attestation reports on assessment of compliance.

Exhibit 35 Servicer compliance statement, filed herewith.

"""

SIGS = """SIGNATURES

Pursuant to the requirements of Section 13 or 15(d) of the Securities Exchange Act of 1934, the registrant has duly caused this report to be signed on its behalf by the undersigned, thereunto duly authorized.

/s/ Officer

"""

PART_IV = """PART IV

Item 15. Exhibits, Financial Statement Schedules.

(a) Exhibits

(35) Servicer compliance statement.

"""

# 0001056404-07-* (EMC / Wells Fargo 2007, text era): plain-text title-matched body block,
# then the EX-35 exhibit's caps heading, which out-scores the body copy (0.80 vs 0.65).
EMC = HEAD + """Item 1117 of Regulation AB, Legal Proceedings.

The registrant knows of no material pending legal proceedings.

Item 1119 of Regulation AB, Affiliations and Certain Relationships and Related Transactions.

No applicable updates.

Item 1122 of Regulation AB, Compliance with Applicable Servicing Criteria.

See Item 15.

Item 1123 of Regulation AB, Servicer Compliance Statement.

See Item 15.

""" + PART_IV + SIGS + INDEX + """EX-35 (b)

ITEM 1123 ANNUAL STATEMENT OF COMPLIANCE

EMC MORTGAGE CORPORATION

I, John Vella, President of EMC Mortgage Corporation, hereby certify that:

""" + FILL + """

IN WITNESS WHEREOF, the undersigned has duly executed this Certificate this 12th day of March 2007.

By: /s/ John Vella
"""

# 0001193125-11..16-*: untitled body labels ("Item 1122 of Regulation AB." with the text
# on the next block) and exhibit-index rows carrying the labels.
INDEX_ROWS = HEAD + """Item 1117 of Regulation AB, Legal Proceedings.

None.

Item 1122 of Regulation AB.

The reports on assessment of compliance are attached as exhibits.

Item 1123 of Regulation AB.

The servicer compliance statements are attached as exhibits.

""" + PART_IV + SIGS + INDEX + """ITEM 1122 REPORT ON ASSESSMENT OF COMPLIANCE WITH APPLICABLE SERVICING CRITERIA

ITEM 1123 CERTIFICATION

""" + FILL + "\n"

# 0001866514-22..26-* / 0001918072-23..26-*: the filer prints its title-matched Reg-AB block
# after the exhibit index, before the signatures.
AFTER_INDEX = HEAD + PART_IV + INDEX + """Item 1112(b). Significant Obligors of Pool Assets.

None.

Item 1117. Legal Proceedings.

None.

Item 1119. Affiliations and Certain Relationships and Related Transactions.

None.

Item 1122. Compliance with Applicable Servicing Criteria.

See the exhibits.

Item 1123. Servicer Compliance Statement.

See the exhibits.

""" + SIGS + FILL + "\n"


def parse(tmp_path, body, *, veto=True):
    p = tmp_path / "d.txt"
    p.write_bytes(body.encode("latin-1"))
    sub = load_text_submission(p, "0000000000-07-000001", 1)
    orig = pipeline.regab_back_matter
    if not veto:
        pipeline.regab_back_matter = lambda *a, **k: []
    try:
        return parse_document(sub, sub.documents[0], "1", grammar=G)
    finally:
        pipeline.regab_back_matter = orig


def regab(r):
    return {n.label_canon: n for n in r.nodes if n.level_kind == "item" and G.is_regab_item(n.label_canon)}


def rej_regab(r):
    return {(x.label_canon, x.raw_start): x.reason for x in r.rejected if x.label_canon and G.is_regab_item(x.label_canon)}


def test_emc_exhibit_heading_rejected_body_item_1123_kept(tmp_path):
    body = EMC
    body_1123 = body.index("Item 1123 of Regulation AB")
    ex_1123 = body.index("ITEM 1123 ANNUAL STATEMENT")
    sigs = body.index("SIGNATURES")
    before = parse(tmp_path, body, veto=False)
    # the defect as shipped in c5b9ca1: the exhibit heading takes the ITEM 1123 slot and
    # drags the boundary into the exhibit
    assert regab(before)["ITEM 1123"].head_raw_start == ex_1123
    assert before.paths["_bounds"]["back_start"] > ex_1123
    after = parse(tmp_path, body)
    n = regab(after)["ITEM 1123"]
    assert n.head_raw_start == body_1123 and "gram.item.title_match" in n.rule_ids
    assert rej_regab(after)[("ITEM 1123", ex_1123)] == "regab_back_matter"
    assert after.paths["_bounds"]["back_start"] <= sigs + len("SIGNATURES") and after.paths["_bounds"]["back_start"] < ex_1123
    assert set(regab(after)) == {"ITEM 1117", "ITEM 1119", "ITEM 1122", "ITEM 1123"}


def test_exhibit_index_rows_rejected(tmp_path):
    body = INDEX_ROWS
    rows = {"ITEM 1122": body.index("ITEM 1122 REPORT"), "ITEM 1123": body.index("ITEM 1123 CERTIFICATION")}
    before = parse(tmp_path, body, veto=False)
    assert {k: regab(before)[k].head_raw_start for k in rows} == rows
    after = parse(tmp_path, body)
    for lab, pos in rows.items():
        assert rej_regab(after)[(lab, pos)] == "regab_back_matter"
        assert lab not in regab(after) or regab(after)[lab].head_raw_start < body.index("SIGNATURES")
    assert after.paths["_bounds"]["back_start"] < rows["ITEM 1122"]


def test_title_matched_block_after_the_exhibit_index_is_kept(tmp_path):
    body = AFTER_INDEX
    before = parse(tmp_path, body, veto=False)
    after = parse(tmp_path, body)
    assert set(regab(after)) == {"ITEM 1112", "ITEM 1117", "ITEM 1119", "ITEM 1122", "ITEM 1123"}
    assert [n.head_raw_start for n in after.nodes] == [n.head_raw_start for n in before.nodes]
    assert after.paths["_bounds"]["back_start"] == before.paths["_bounds"]["back_start"] > body.index("Item 1123.")
    assert "regab_back_matter" not in rej_regab(after).values()


def test_seq_regab_run_on_nodes_the_reg_ab_run_placed(tmp_path):
    after = parse(tmp_path, EMC)
    for lab, n in regab(after).items():
        assert "seq.regab_run" in n.rule_ids, lab
    assert not any("seq.regab_run" in n.rule_ids for n in after.nodes
                   if n.level_kind == "item" and not G.is_regab_item(n.label_canon))


def test_plain_10k_parse_never_rebuilds(tmp_path, monkeypatch):
    calls = []
    real = pipeline.build_tree
    monkeypatch.setattr(pipeline, "build_tree", lambda *a, **k: calls.append(1) or real(*a, **k))
    parse(tmp_path, HEAD + PART_IV + SIGS + INDEX + FILL)
    parse(tmp_path, AFTER_INDEX)
    assert len(calls) == 2
    calls.clear()
    parse(tmp_path, EMC)
    assert len(calls) == 2  # one build, one rebuild


def test_out_of_order_ordinary_item_never_tagged_regab_run(tmp_path):
    # the smoke found seq.regab_run on 14 out-of-order ITEM 1B nodes (the extras loop
    # reused the chain loop's flag); an ordinary item recovered by seq.out_of_order
    # after a Reg-AB block must carry no Reg-AB tag
    # the Reg-AB item is the LAST candidate the chains place, so a flag leaking out of the
    # chain loop would land on the out-of-order ITEM 1A placed after it
    body = HEAD.replace("Item 1A. Risk Factors.\n\nOmitted.\n\n", "") + PART_IV + """Item 1A. Risk Factors.

Omitted.

Item 1117 of Regulation AB, Legal Proceedings.

None.

""" + SIGS + INDEX + FILL
    r = parse(tmp_path, body)
    for n in r.nodes:
        if n.level_kind == "item" and not G.is_regab_item(n.label_canon):
            assert "seq.regab_run" not in n.rule_ids, (n.label_canon, n.rule_ids)
    items = {n.label_canon: n for n in r.nodes if n.level_kind == "item"}
    assert "seq.out_of_order" in items["ITEM 1A"].rule_ids
    assert "seq.regab_run" in regab(r)["ITEM 1117"].rule_ids
