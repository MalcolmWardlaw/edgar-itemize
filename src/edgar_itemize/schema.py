from __future__ import annotations

import pyarrow as pa

NODE_SCHEMA = pa.schema(
    [
        ("accession_number", pa.string()),
        ("cik", pa.string()),
        ("sequence", pa.int32()),
        ("doc_type", pa.string()),
        ("grammar", pa.string()),
        ("node_id", pa.int32()),
        ("parent_id", pa.int32()),
        ("depth", pa.int8()),
        ("level_kind", pa.string()),
        ("label_canon", pa.string()),
        ("label_raw", pa.string()),
        ("title", pa.string()),
        ("raw_start", pa.int64()),
        ("raw_end", pa.int64()),
        ("head_raw_start", pa.int64()),
        ("head_raw_end", pa.int64()),
        ("norm_start", pa.int64()),
        ("norm_end", pa.int64()),
        ("order_key", pa.int32()),
        ("confidence", pa.float32()),
        ("rule_ids", pa.list_(pa.string())),
        ("path", pa.list_(pa.int16())),
        ("path_str", pa.string()),
        ("meta", pa.int8()),
        # Turn 7 (d): which glued agreement this node belongs to, orthogonal to meta.
        # 0 = the primary/only agreement; 1, 2, ... each subsequent compound exhibit.
        ("segment", pa.int8()),
        # A2 incorporation-by-reference flags: set only on item nodes whose
        # tag-stripped span is small enough to be a pointer stub; null elsewhere.
        ("item_incorporated_by_reference", pa.bool_()),
        ("ibr_target", pa.string()),  # "ex13" | "annual_report" | null
        ("ibr_target_in_submission", pa.bool_()),  # EX-13 <TYPE> present in the same raw submission
        ("item_cross_reference", pa.bool_()),  # internal pointer/IBR: content elsewhere in this document
        # EX-13 heading nodes only (Turn 7 b): "ITEM 6"/"ITEM 7"/"ITEM 8" the heading's
        # content satisfies per the ars kind->item mapping (ars_kind.py); null elsewhere.
        ("satisfies_item", pa.string()),
        # Turn 7 (c): additional item keys ("2", "7A", ...) a multi-item heading
        # ("Items 1 and 2") covers, derived from the node's own `multi.ITEM <k>`
        # rule ids. label_canon stays the first item printed; null/empty when the
        # node is not a multi-item label.
        ("covers_items", pa.list_(pa.string())),
        ("profile_era", pa.string()),
        ("profile_publisher", pa.string()),
        ("agent_cik", pa.string()),
        ("parser_version", pa.string()),
        ("normalizer_version", pa.string()),
    ]
)

DOC_SCHEMA = pa.schema(
    [
        ("accession_number", pa.string()),
        ("cik", pa.string()),
        ("sequence", pa.int32()),
        ("doc_type", pa.string()),
        ("grammar", pa.string()),
        ("filed_year", pa.int32()),
        ("profile_era", pa.string()),
        ("profile_publisher", pa.string()),
        ("agent_cik", pa.string()),
        ("signals", pa.string()),  # json
        ("n_blocks", pa.int32()),
        ("n_candidates", pa.int32()),
        ("n_nodes", pa.int32()),
        ("toc_found", pa.bool_()),
        ("items_found", pa.list_(pa.string())),
        ("doc_raw_start", pa.int64()),
        ("doc_raw_end", pa.int64()),
        ("front_end", pa.int64()),
        ("back_start", pa.int64()),
        ("n_segments", pa.int32()),
        ("level_profile", pa.string()),
        ("normalized_text", pa.large_string()),  # >2GB per partition when text is kept
        ("parser_version", pa.string()),
        ("normalizer_version", pa.string()),
        ("error", pa.string()),
        # R0 (docs/RELEASE_PLAN.md section 3.2): the unit of reproducibility is the raw
        # submission file. Null on error rows (file missing / no primary document).
        ("input_sha256", pa.string()),
        ("input_bytes", pa.int64()),
        # D9 (docs/RELEASE_PLAN.md section 9): `cik` is the header's first FILER CIK; this
        # is the CIK the manifest listed, i.e. the mirror directory the file was read from.
        ("manifest_cik", pa.string()),
    ]
)

REJECTED_SCHEMA = pa.schema(
    [
        ("accession_number", pa.string()),
        ("sequence", pa.int32()),
        ("block_idx", pa.int32()),
        ("kind", pa.string()),
        ("label_canon", pa.string()),
        ("score", pa.float32()),
        ("reason", pa.string()),
        ("raw_start", pa.int64()),
        ("text", pa.string()),
    ]
)
