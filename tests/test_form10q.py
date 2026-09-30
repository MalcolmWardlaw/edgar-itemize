from edgar_itemize.grammar.form10q import ITEM_RE, Form10QGrammar

G = Form10QGrammar()


def canon(s):
    m = ITEM_RE.match(s)
    return G.canonicalize("item", m) if m else None


def test_part_decided_by_title():
    assert canon("Item 1. Financial Statements") == "ITEM I.1"
    assert canon("Item 1. Legal Proceedings") == "ITEM II.1"
    assert canon("Item 1A. Risk Factors") == "ITEM II.1A"
    assert canon("Item 6. Exhibits") == "ITEM II.6"
    assert canon("Item 2. Management's Discussion and Analysis") == "ITEM I.2"
    assert canon("Item 2. Unregistered Sales of Equity Securities") == "ITEM II.2"


def test_ambiguous_without_title():
    assert canon("Item 3.") == "ITEM ?.3"
    assert canon("Item 5.") == "ITEM II.5"
    assert canon("Item 7. Management's Discussion") is None


def test_order_keys():
    assert G.order_key("item", "ITEM I.4") < G.order_key("item", "ITEM II.1") < G.order_key("item", "ITEM II.1A") < G.order_key("item", "ITEM II.6")


# --- Turn 7 (e): three-tier expected_items() --------------------------------


def test_expected_items_pre_mandate_1994():
    assert G.expected_items(1994) == ("I.1", "I.2", "II.6")


def test_expected_items_never_includes_omittable():
    for year in (1994, 1998, 2003, 2006, 2024):
        assert not (set(G.expected_items(year)) & set(G.OMITTABLE))
    assert "II.1" not in G.expected_items(2024)  # the actual bug this tier fixes


def test_expected_items_i3_boundary_1998():
    assert "I.3" not in G.expected_items(1997)
    assert "I.3" in G.expected_items(1998)


def test_expected_items_i4_boundary_2003():
    assert "I.4" not in G.expected_items(2002)
    assert "I.4" in G.expected_items(2003)


def test_expected_items_ii1a_boundary_2006():
    assert "II.1A" not in G.expected_items(2005)
    assert "II.1A" in G.expected_items(2006)


def test_expected_items_modern_year_has_all_required_tiers():
    assert set(G.expected_items(2024)) == {"I.1", "I.2", "I.3", "I.4", "II.1A", "II.6"}


def test_omittable_items_constant_across_eras():
    assert G.omittable_items(1994) == G.omittable_items(2024) == ("II.1", "II.2", "II.3", "II.4", "II.5")


# --- Turn 8: gram.item.4t (transitional internal-control numbering, 2007-2010) ------


def test_item_4t_canonicalizes_to_item_i4():
    assert canon("Item 4T. Controls and Procedures") == "ITEM I.4"
    assert canon("ITEM 4T.") == "ITEM I.4"
    assert canon("Item 4t. Controls and Procedures") == "ITEM I.4"


def test_item_4t_label_rules_tagged():
    m = ITEM_RE.match("Item 4T. Controls and Procedures")
    assert G.label_rules("item", m) == ["gram.item.4t"]


def test_ordinary_item_4_untagged_by_4t_rule():
    m = ITEM_RE.match("Item 4. Controls and Procedures")
    assert G.canonicalize("item", m) == "ITEM I.4"
    assert G.label_rules("item", m) == []


def test_t_suffix_only_valid_on_item_4():
    # no other item number ever used transitional "T" numbering -- reject rather
    # than guess (e.g. a stray "Item 1T" is not a real SEC-recognized label)
    assert canon("Item 1T. Something") is None
    assert canon("Item 2T.") is None


def test_item_4t_order_key_matches_item_4():
    m = ITEM_RE.match("Item 4T. Controls and Procedures")
    canon4t = G.canonicalize("item", m)
    assert G.order_key("item", canon4t) == G.order_key("item", "ITEM I.4")
