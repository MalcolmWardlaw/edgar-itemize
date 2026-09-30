from edgar_itemize.tree import max_weight_increasing


def test_prefers_heavier_chain():
    # TOC chain (weight .4 each) then body chain (weight .8 each)
    items = [(10, 0.4), (20, 0.4), (30, 0.4), (10, 0.8), (20, 0.8), (30, 0.8)]
    assert max_weight_increasing(items) == [3, 4, 5]


def test_strictly_increasing_and_ties_prefer_later():
    items = [(10, 0.5), (10, 0.5), (20, 0.5)]
    assert max_weight_increasing(items) == [1, 2]


def test_mixed_chain_allowed():
    # body Item 1 first, a stray cross-ref Item 3 (low), then body 2, 3
    items = [(10, 0.9), (30, 0.1), (20, 0.9), (30, 0.9)]
    assert max_weight_increasing(items) == [0, 2, 3]


def test_empty():
    assert max_weight_increasing([]) == []
