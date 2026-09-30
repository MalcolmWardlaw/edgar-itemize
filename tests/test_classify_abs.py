"""Two-tier ABS/Reg-AB signal (case A1/D1) and A2 IBR item flags."""

import pytest

from edgar_itemize.classify import _REG_AB_WINDOW, _SF_NAME_RE, abs_two_tier, classify, ibr_flags
from edgar_itemize.sgml import DocumentBlock, conformed_name


def _doc(is_html=False):
    return DocumentBlock(sequence=1, type="10-K", filename=None, description=None,
                         is_html=is_html, doc_start=0, doc_end=0, text_start=0, text_end=0)


def _classify(body, name=""):
    return classify("0000000001-05-000001", "12345", _doc(), body, company_name=name)


def test_specific_tier_flags_trust_vocabulary():
    body = "This report is filed under Regulation AB. Item 1122 Servicing Criteria assessments follow. The Issuing Entity has no employees."
    p = _classify(body)
    assert p.signals["reg_ab_specific"] >= 2
    assert abs_two_tier(p.signals)


def test_generic_asset_backed_alone_does_not_flag():
    # A1's headline FP mechanism: bare "asset-backed" prose (Anthem, Disney, insurers).
    body = "The portfolio includes asset-backed securities. " * 10
    p = _classify(body)
    assert p.signals["reg_ab_specific"] == 0
    assert p.signals["reg_ab_generic"] == 10
    assert not abs_two_tier(p.signals)


def test_relaxed_depositor_catches_principal_life_style():
    # FN mechanism: body says "as depositor" (twice), zero Reg-AB vocabulary.
    body = "Principal Life Income Fundings Trust 2005-26, as depositor. The registrant acts as depositor of the notes."
    p = _classify(body)
    assert p.signals["reg_ab_specific"] >= 2
    assert abs_two_tier(p.signals)


def test_noteholders_and_pooling_are_specific():
    body = "Distributions to Noteholders are governed by the Pooling and Servicing Agreement."
    p = _classify(body)
    assert p.signals["reg_ab_specific"] >= 2
    assert abs_two_tier(p.signals)


def test_specific_tier_window_is_front_loaded():
    body = " " * (_REG_AB_WINDOW + 10) + "Regulation AB Servicing Criteria Issuing Entity"
    p = _classify(body)
    assert p.signals["reg_ab_specific"] == 0


@pytest.mark.parametrize(
    "name,expected",
    [
        ("PRINCIPAL LIFE INCOME FUNDINGS TRUST 2005-26", True),
        ("CARMAX AUTO OWNER TRUST 2017-1", True),
        ("CIT EQUIPMENT COLLATERAL 2005-VT1", True),
        ("USAA AUTO OWNER TRUST 2002-1", True),
        ("RURAL ELECTRIC COOPERATIVE GRANTOR TRUST KEPCO", True),
        ("SLM STUDENT LOAN TRUST 2005-5", True),
        ("FINANCIAL ASSET SECURITIES CORP", False),  # depositor shell A1's pattern knowingly misses
        ("HYUNDAI ABS FUNDING CORP", True),
        # deliberately NOT bare "TRUST" — REIT-like names must not match
        ("STARWOOD PROPERTY TRUST", False),
        ("HOSPITALITY PROPERTIES TRUST", False),
        ("WALT DISNEY CO", False),
        ("ANTHEM, INC.", False),
        ("SAFETY INSURANCE GROUP INC", False),
    ],
)
def test_name_tier_pattern(name, expected):
    assert bool(_SF_NAME_RE.search(name)) == expected


def test_name_tier_flags_via_classify():
    p = _classify("A skeleton 10-K with no distinctive vocabulary.", name="PRINCIPAL LIFE INCOME FUNDINGS TRUST 2005-26")
    assert p.signals["reg_ab_name"] == 1
    assert abs_two_tier(p.signals)
    p = _classify("An ordinary operating 10-K.", name="WALT DISNEY CO")
    assert p.signals["reg_ab_name"] == 0
    assert not abs_two_tier(p.signals)


def test_abs_two_tier_legacy_fallback():
    # parquet written before the fix has only the collapsed reg_ab count
    assert abs_two_tier({"reg_ab": 3})
    assert not abs_two_tier({"reg_ab": 2})
    # new-format signals ignore the legacy count entirely (Anthem: reg_ab=10, all generic)
    assert not abs_two_tier({"reg_ab": 10, "reg_ab_specific": 0, "reg_ab_name": 0, "reg_ab_generic": 10})


def test_conformed_name():
    hdr = "ACCESSION NUMBER: 0000000001-05-000001\nCOMPANY CONFORMED NAME:\t\tIBP INC\nCENTRAL INDEX KEY: 0000052477\n"
    assert conformed_name(hdr) == "IBP INC"
    assert conformed_name("no name here") == ""


# --- A2 item-level IBR flags ---


def test_ibr_external_annual_report():
    # 0000950144-96-003765 wording
    t = ("The consolidated financial statements and notes thereto on pages 23 to 33 of the "
         "1996 Annual Report to Shareholders are incorporated herein by reference.")
    f = ibr_flags(t)
    assert f["item_incorporated_by_reference"] is True
    assert f["ibr_target"] == "annual_report"
    assert f["item_cross_reference"] is False


def test_ibr_external_exhibit_13():
    # 0000916002-01-500100 wording
    t = ("Incorporated by reference to the consolidated financial statements of the Annual Report, "
         "copies of which pages are included in Exhibit 13 to this Report.")
    f = ibr_flags(t)
    assert f["item_incorporated_by_reference"] is True
    assert f["ibr_target"] == "ex13"


def test_ibr_internal_context_not_flagged():
    # 0001193125-22-090045 counter-example: internal IBR must NOT be flagged external
    t = ("This information appears following Item 15 of this Annual Report on Form 10-K "
         "and is incorporated herein by reference.")
    f = ibr_flags(t)
    assert f["item_incorporated_by_reference"] is False
    assert f["ibr_target"] is None
    assert f["item_cross_reference"] is True


def test_ibr_internal_pointer_without_verb():
    t = "See Index to Financial Statements on page F-1."
    f = ibr_flags(t)
    assert f["item_incorporated_by_reference"] is False
    assert f["item_cross_reference"] is True


def test_ibr_not_applicable_is_neither():
    f = ibr_flags("Not applicable.")
    assert f["item_incorporated_by_reference"] is False
    assert f["item_cross_reference"] is False


def test_ibr_large_span_not_a_candidate():
    assert ibr_flags("word " * 2000) is None
