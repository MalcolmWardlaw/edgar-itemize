from edgar_itemize.blocks import Block
from edgar_itemize.headings import _signature


def blk(text, **kw):
    b = Block(idx=0, text=text, raw_start=0, raw_end=len(text), norm_start=0, norm_end=len(text), lines=tuple(text.split("\n")))
    for k, v in kw.items():
        setattr(b, k, v)
    return b


def test_bold_short_is_heading():
    assert _signature(blk("Results of Operations", bold=True), "html_publisher") is not None


def test_plain_paragraph_is_not():
    assert _signature(blk("We sell widgets to customers in many countries and expect growth.", bold=False), "html_publisher") is None


def test_leadin_rejected():
    assert _signature(blk("The following table sets forth:", bold=True), "html_publisher") is None


def test_date_and_signature_noise_rejected():
    assert _signature(blk("February 11, 2004", center=True), "html_publisher") is None
    assert _signature(blk("/s/ Arthur Andersen LLP", center=True), "html_publisher") is None


def test_text_era_standalone_title():
    b = blk("Liquidity and Capital Resources", blank_before=True, blank_after=True)
    sig = _signature(b, "text")
    assert sig is not None and "sty.standalone" in sig[1]
