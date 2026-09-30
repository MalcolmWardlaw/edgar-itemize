"""Local-LLM judge: proposes labels and explanations; never part of the parser.

Talks to an Ollama server named by EDGAR_ITEMIZE_OLLAMA (no default; an OpenAI-compatible
server for "mlx:" model names is named by EDGAR_ITEMIZE_MLX). Every call asks for strict
JSON and records model, prompt hash and raw reply so verdicts are auditable. Needs the
`judge` extra (httpx).
"""

from __future__ import annotations

import hashlib
import json
import os
import re as _re
from dataclasses import dataclass

OLLAMA_URL = os.environ.get("EDGAR_ITEMIZE_OLLAMA")  # required when chat_json is called
DEFAULT_MODEL = os.environ.get("EDGAR_ITEMIZE_MODEL", "gpt-oss:120b")
# OpenAI-compatible server for models not in Ollama (mlx_lm.server); select with model "mlx:<served id>"
MLX_URL = os.environ.get("EDGAR_ITEMIZE_MLX")  # required when an "mlx:" model is used


def _require_url(url: str | None, env: str, what: str) -> str:
    if not url:
        raise SystemExit(f"{env} is not set: export {env}=http://host:port ({what})")
    return url


def _httpx():
    try:
        import httpx
    except ImportError as e:  # pragma: no cover
        raise SystemExit(f"{e}\nthe judge needs the optional dependencies: uv sync --extra judge  (or pip install 'edgar-itemize[judge]')")
    return httpx


@dataclass
class Verdict:
    model: str
    prompt_sha: str
    answer: dict
    raw: str


def _loads_reply(raw: str) -> dict:
    """Parse a JSON reply, tolerating a <think> block or a ```json fence (servers without constrained decoding)."""
    text = _re.sub(r"<think>.*?</think>", "", raw, flags=_re.DOTALL).strip()
    fence = _re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=_re.DOTALL)
    if fence:
        text = fence.group(1)
    elif not text.startswith("{") and "{" in text:
        text = text[text.index("{"): text.rindex("}") + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_unparsed": raw}


def _chat_openai(model: str, messages: list[dict], schema: dict | None, timeout: float) -> str:
    """OpenAI-compatible /v1/chat/completions (mlx_lm.server, LM Studio). Model names prefixed `mlx:`."""
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 2048,
        "stream": False,
        # qwen3 chat templates switch reasoning off with this; servers that do not know it ignore it
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if schema:
        # mlx_lm.server ignores response_format, so the model never sees the schema that Ollama's
        # grammar enforces; state it in the system message so the reply carries the same fields
        note = "Reply with one JSON object matching this JSON schema, nothing else: " + json.dumps(schema)
        if messages and messages[0]["role"] == "system":
            messages = [{"role": "system", "content": messages[0]["content"] + "\n\n" + note}] + messages[1:]
        else:
            messages = [{"role": "system", "content": note}] + messages
        body["messages"] = messages
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "verdict", "schema": schema}}
    r = _httpx().post(f"{_require_url(MLX_URL, 'EDGAR_ITEMIZE_MLX', 'the OpenAI-compatible server for mlx: models')}/v1/chat/completions", json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


def chat_json(prompt: str, *, system: str = "", model: str = DEFAULT_MODEL, schema: dict | None = None, timeout: float = 300.0, think: bool = False) -> Verdict:
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    sha = hashlib.sha256((system + prompt).encode()).hexdigest()[:16]
    if model.startswith("mlx:"):
        raw = _chat_openai(model[len("mlx:"):], messages, schema, timeout)
        return Verdict(model=model, prompt_sha=sha, answer=_loads_reply(raw), raw=raw)
    body = {
        "model": model,
        "messages": ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}],
        "stream": False,
        "format": schema or "json",
        "options": {"temperature": 0, "num_ctx": 16384},
    }
    # gpt-oss cannot switch reasoning off: Ollama >= 0.33 returns empty content for
    # think=False, so only send the flag to models that honour it (qwen3 family).
    if not model.startswith("gpt-oss"):
        body["think"] = think
    r = _httpx().post(f"{_require_url(OLLAMA_URL, 'EDGAR_ITEMIZE_OLLAMA', 'the Ollama server the judge talks to')}/api/chat", json=body, timeout=timeout)
    r.raise_for_status()
    raw = r.json()["message"]["content"]
    try:
        ans = json.loads(raw)
    except json.JSONDecodeError:
        ans = {"_unparsed": raw}
    return Verdict(model=model, prompt_sha=hashlib.sha256((system + prompt).encode()).hexdigest()[:16], answer=ans, raw=raw)


HEADING_SCHEMA = {
    "type": "object",
    "properties": {
        "is_heading": {"type": "boolean"},
        "kind": {"type": "string", "enum": ["heading", "table_of_contents", "cross_reference", "continuation_page_header", "other"]},
        "label": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["is_heading", "kind", "reason"],
}

HEADING_SYSTEM = (
    "You judge whether a line in an SEC filing is the actual section heading that begins that section's text. "
    "A table-of-contents entry, a cross-reference such as 'see Item 7', or a mention inside a sentence is not a heading. "
    "A running page header or footer is not a heading either: in printed filings every page can open with a repeat of the "
    "current section's title, often followed by '(continued)' or '(cont'd)' and a page number, and such a repeat marks a "
    "page break, not the place where the section begins; call it continuation_page_header. Answer only with JSON."
)


def heading_prompt(label: str, window: str, marker: str = ">>>") -> str:
    return (
        f"The line marked {marker} is a candidate heading for '{label}'. Using the surrounding text, decide whether it is the "
        f"actual heading where the section begins (not a table-of-contents entry, not a cross-reference).\n\n{window}"
    )


# ---------------------------------------------------------------------------
# Turn 5-7 judge tasks. Each task is (schema, system prompt, prompt builder,
# answer flattener). The batch driver picks the task from the window record.
# ---------------------------------------------------------------------------

SUBHEADING_KINDS = ["standalone_subheading", "runin_subheading", "page_header_or_continuation",
                    "table_caption_or_furniture", "toc_entry", "body_text", "other"]
SUBHEADING_SCHEMA = {
    "type": "object",
    "properties": {
        "is_heading": {"type": "boolean"},
        "kind": {"type": "string", "enum": SUBHEADING_KINDS},
        "heading_text": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["is_heading", "kind", "reason"],
}
SUBHEADING_SYSTEM = (
    "You judge whether a marked passage inside an Item of an SEC Form 10-K is a sub-heading: a short title that begins a "
    "subsection of the text below the Item level. Kinds: standalone_subheading (a title on its own line); runin_subheading "
    "(a short bold, underlined or italic lead-in phrase, usually ending in a period or colon, that opens a paragraph and is "
    "followed by ordinary prose in the same paragraph); page_header_or_continuation (a running page header or footer: printed filings "
    "open every page with a repeat of the current section's title, often followed by '(continued)' or '(cont'd)' and a page "
    "number; such a repeat marks a page break, not a new subsection); table_caption_or_furniture (table titles, column headers, page "
    "numbers, 'Table of Contents' links); toc_entry; body_text (ordinary prose, a full sentence, a list item); other. "
    "is_heading is true only for standalone_subheading and runin_subheading. For a runin_subheading, heading_text is the "
    "lead-in phrase alone. Answer only with JSON."
)


def subheading_prompt(context_label: str, window: str, proposed: str = "", marker: str = ">>>") -> str:
    where = f"inside {context_label} of" if context_label else "in"
    prop = f" The proposed heading text is '{proposed}'." if proposed else ""
    return (
        f"The passage below is {where} a Form 10-K. The block marked {marker} is a candidate sub-heading.{prop} "
        f"Decide what kind of thing the marked block is.\n\n{window}"
    )


CONTRACT_KINDS = ["article", "section", "clause", "definition_term", "toc_entry", "cross_reference", "body_text",
                  "signature_or_back_matter", "other"]
CONTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "is_heading": {"type": "boolean"},
        "kind": {"type": "string", "enum": CONTRACT_KINDS},
        "label": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["is_heading", "kind", "reason"],
}
CONTRACT_SYSTEM = (
    "You judge whether a marked line in a credit agreement or similar contract filed with the SEC is a structural heading "
    "that begins a unit of the agreement. Kinds: article (ARTICLE I, ARTICLE 5 ...); section (Section 2.01, SECTION 4. ...); "
    "clause (an enumerated sub-paragraph such as (a), (ii), (B), 1., that begins a unit of text); definition_term (a "
    "defined term in quotes opening a definition paragraph); toc_entry (a table-of-contents row, often with a page number); "
    "cross_reference (a mention such as 'pursuant to Section 2.01' inside a sentence); body_text; signature_or_back_matter "
    "(signature blocks, schedules, exhibits, notarial text); other. is_heading is true for article, section and clause "
    "only. label is the heading's own label as written (e.g. 'SECTION 2.01', '(b)'). Answer only with JSON."
)


def contract_prompt(label: str, window: str, marker: str = ">>>") -> str:
    return (
        f"The line marked {marker} is a candidate heading labelled '{label}'. Using the surrounding text, decide what kind "
        f"of thing it is and whether it begins a structural unit of the agreement.\n\n{window}"
    )


PARENT_RELATIONS = ["restarts_sibling_list", "opens_child_list", "continues_list", "not_a_clause"]
PARENT_SCHEMA = {
    "type": "object",
    "properties": {
        "parent_label": {"type": "string"},
        "relation": {"type": "string", "enum": PARENT_RELATIONS},
        "reason": {"type": "string"},
    },
    "required": ["parent_label", "relation", "reason"],
}
PARENT_SYSTEM = (
    "You read a passage of a contract and place a marked enumerated clause in the outline. parent_label is the label of the "
    "heading or clause the marked clause sits directly under (e.g. 'SECTION 4.02', '(c)', 'ARTICLE III'); write 'document' "
    "if none. relation says how the marked clause relates to the list that precedes it: continues_list (the next label in "
    "the list already open, e.g. (b) after (a)); opens_child_list (the first label of a new list nested under the previous "
    "text, e.g. (i) under (a)); restarts_sibling_list (a first label, like (a) or (i), that begins a NEW list at the same "
    "level as an earlier list, because a new section, paragraph or defined term started in between); not_a_clause (the "
    "marked text is not an enumerated clause). Answer only with JSON."
)


def parent_prompt(label: str, window: str, marker: str = ">>>") -> str:
    return (
        f"The clause marked {marker} is labelled '{label}'. Using the text before it, say which heading or clause it sits "
        f"directly under and how it relates to the preceding list.\n\n{window}"
    )


BOUNDARY_KINDS = ["main_body_continues", "back_matter_begins", "signatures", "exhibit_index",
                  "bound_annual_report_begins", "financial_statements_begin", "new_agreement_begins"]
BOUNDARY_SCHEMA = {
    "type": "object",
    "properties": {
        "boundary": {"type": "string", "enum": BOUNDARY_KINDS},
        "reason": {"type": "string"},
    },
    "required": ["boundary", "reason"],
}
BOUNDARY_SYSTEM = (
    "You judge what begins at a marked point in an SEC filing. main_body_continues: the numbered structure of the document "
    "(Items, Articles, Sections) carries on past the mark. back_matter_begins: schedules, exhibits, annexes or other "
    "appended material begin. signatures: the signature block or 'IN WITNESS WHEREOF' execution page begins. "
    "exhibit_index: an index or list of exhibits begins. bound_annual_report_begins: a separate annual report to "
    "shareholders (glossy report, letter to shareholders) is bound in from here. financial_statements_begin: the audited "
    "financial statements section (auditor's report, balance sheets, notes) begins. new_agreement_begins: a different "
    "agreement, amendment or exhibit with its own title and numbering starts here (compound exhibits). Answer only with JSON."
)


def boundary_prompt(window: str, marker: str = ">>>") -> str:
    return f"Decide what begins at the point marked {marker}.\n\n{window}"


ARS_KINDS = ["mdna", "financial_statements", "notes_to_financial_statements", "auditors_report", "selected_financial_data",
             "letter_to_shareholders", "business_description", "other"]
ARS_SCHEMA = {
    "type": "object",
    "properties": {
        "is_heading": {"type": "boolean"},
        "level": {"type": "string", "enum": ["section", "subheading", "none"]},
        "kind": {"type": "string", "enum": ARS_KINDS},
        "reason": {"type": "string"},
    },
    "required": ["is_heading", "level", "kind", "reason"],
}
ARS_SYSTEM = (
    "You judge a marked line in an Annual Report to Shareholders (Exhibit 13 to a Form 10-K). Say whether it is a section "
    "heading that begins a section of the report, and which section: mdna (management's discussion and analysis), "
    "financial_statements (balance sheets, statements of income / operations / cash flows / equity), "
    "notes_to_financial_statements, auditors_report (report of independent accountants / registered public accounting firm), "
    "selected_financial_data (five- or ten-year summaries), letter_to_shareholders, business_description, other. "
    "Set level to 'section' only when the line is the title that opens one of those major sections (e.g. the "
    "'Management's Discussion and Analysis' title, 'Consolidated Balance Sheets', 'Notes to Consolidated Financial "
    "Statements', 'Report of Independent Accountants'); set level to 'subheading' when it is a heading inside one of "
    "them (a topic within MD&A, a note title or accounting-policy heading, a statement line); set level to 'none' when "
    "it is not a heading. kind is the section the line opens or belongs to. "
    "A table caption, column header, page header or a sentence is not a heading. Answer only with JSON."
)


def ars_prompt(window: str, marker: str = ">>>") -> str:
    return f"The line marked {marker} is a candidate heading in an annual report to shareholders. Decide whether it begins a section and which one.\n\n{window}"


BOUNDARY_BINARY_KINDS = ["agreement", "amendment", "guaranty", "security_or_pledge", "mortgage_or_deed",
                         "note_or_warrant", "form_exhibit", "schedule_or_annex", "other", "none"]
BOUNDARY_BINARY_SCHEMA = {
    "type": "object",
    "properties": {
        "new_instrument": {"type": "boolean"},
        "kind": {"type": "string", "enum": BOUNDARY_BINARY_KINDS},
        "reason": {"type": "string"},
    },
    "required": ["new_instrument", "kind", "reason"],
}
BOUNDARY_BINARY_SYSTEM = (
    "A compound exhibit binds several separate instruments together in one filed document. You judge whether a DIFFERENT "
    "instrument begins at the point marked in the text below: one with its own title, its own parties or recitals, and its "
    "own numbering, distinct from whatever precedes the mark. An 'Exhibit B form of ...' attached to the same deal still "
    "counts as a different instrument if it has its own title and its own numbering. A cross-reference to another document, "
    "a statutory citation (e.g. 'Article 9 of the Uniform Commercial Code'), a mid-sentence numeral, or the continuation of "
    "the same instrument's own numbering is NOT a new instrument. Set new_instrument to true only when a different, "
    "self-contained instrument actually starts at the mark. kind names what begins there when new_instrument is true "
    "(agreement, amendment, guaranty, security_or_pledge, mortgage_or_deed, note_or_warrant, form_exhibit, schedule_or_annex, "
    "other); use 'none' when new_instrument is false. Answer only with JSON."
)


def boundary_binary_prompt(window: str, marker: str = ">>>") -> str:
    return f"Does a different instrument, with its own title, parties/recitals and numbering, begin at the point marked {marker}?\n\n{window}"


def _flat_boundary_binary(a: dict) -> dict:
    return dict(is_heading=None, new_instrument=a.get("new_instrument"), llm_kind=a.get("kind"), reason=a.get("reason"))


def _flat_heading(a: dict) -> dict:
    return dict(is_heading=a.get("is_heading"), llm_kind=a.get("kind"), reason=a.get("reason"), llm_label=a.get("label"))


def _flat_ars(a: dict) -> dict:
    return dict(is_heading=a.get("is_heading"), llm_level=a.get("level"), llm_kind=a.get("kind"), reason=a.get("reason"))


def _flat_subheading(a: dict) -> dict:
    return dict(is_heading=a.get("is_heading"), llm_kind=a.get("kind"), reason=a.get("reason"), heading_text=a.get("heading_text"))


def _flat_parent(a: dict) -> dict:
    return dict(is_heading=None, llm_kind=a.get("relation"), parent_label=a.get("parent_label"), reason=a.get("reason"))


def _flat_boundary(a: dict) -> dict:
    return dict(is_heading=None, llm_kind=a.get("boundary"), reason=a.get("reason"))


# task name -> (schema, system, prompt builder taking the window record, flattener)
TASKS = {
    "heading": (HEADING_SCHEMA, HEADING_SYSTEM, lambda w: heading_prompt(w["label"], w["window"]), _flat_heading),
    "subheading": (SUBHEADING_SCHEMA, SUBHEADING_SYSTEM, lambda w: subheading_prompt(w.get("context", ""), w["window"], w.get("proposed", "")), _flat_subheading),
    "contract": (CONTRACT_SCHEMA, CONTRACT_SYSTEM, lambda w: contract_prompt(w["label"], w["window"]), _flat_heading),
    "parent": (PARENT_SCHEMA, PARENT_SYSTEM, lambda w: parent_prompt(w["label"], w["window"]), _flat_parent),
    "boundary": (BOUNDARY_SCHEMA, BOUNDARY_SYSTEM, lambda w: boundary_prompt(w["window"]), _flat_boundary),
    "boundary_binary": (BOUNDARY_BINARY_SCHEMA, BOUNDARY_BINARY_SYSTEM, lambda w: boundary_binary_prompt(w["window"]), _flat_boundary_binary),
    "ars": (ARS_SCHEMA, ARS_SYSTEM, lambda w: ars_prompt(w["window"]), _flat_ars),
}


def judge_window(w: dict, *, model: str = DEFAULT_MODEL) -> dict:
    """Judge one window record (task, window, label ...) and return the flattened verdict fields."""
    schema, system, build, flat = TASKS[w.get("task", "heading")]
    v = chat_json(build(w), system=system, model=model, schema=schema)
    out = flat(v.answer)
    out.update(model=v.model, prompt_sha=v.prompt_sha)
    if "_unparsed" in v.answer:
        out["error"] = "unparsed reply"
    return out


# ---------------------------------------------------------------------------
# Window hygiene (casebook C3 M2): glued "Table of Contents" back-links and
# standalone furniture lines made judges mislabel real headings as TOC entries.
# ---------------------------------------------------------------------------
import re as _re

_FURNITURE_LINE = _re.compile(r"^\s*(?:table of contents|back to (?:top|index|contents)|return to (?:top|index|table of contents)|index)\s*$", _re.IGNORECASE)
_GLUE_TAIL = _re.compile(r"\s*(?:table of contents|back to (?:top|index|contents)|return to (?:top|index))\s*$", _re.IGNORECASE)


def clean_window(text: str, marker: str = ">>>") -> str:
    """Drop standalone furniture lines; strip back-link glue from the marked line."""
    out = []
    for line in text.split("\n"):
        if _FURNITURE_LINE.match(line):
            continue
        if line.startswith(marker):
            core = line[len(marker):]
            stripped = _GLUE_TAIL.sub("", core)
            if stripped.strip():
                line = marker + stripped
        out.append(line)
    return "\n".join(out)
