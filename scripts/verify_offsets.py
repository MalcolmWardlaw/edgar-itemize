"""Round-trip check: every node's heading slice in the raw file must contain its label."""
import re, sys
from pathlib import Path
import pyarrow.parquet as pq
from edgar_itemize.sgml import read_submission

run = Path(sys.argv[1]); kind = sys.argv[2] if len(sys.argv) > 2 else "10k"
manifest = sys.argv[3] if len(sys.argv) > 3 else "gold/sample_manifest_10k.parquet"
paths = {r["accession_number"]: r["archive_path"] for r in pq.read_table(manifest).to_pylist()}
nodes = pq.read_table(run / f"nodes-{kind}.parquet").to_pylist()
cache = {}; bad = 0; n = 0
for x in nodes:
    if x["level_kind"] not in ("item", "part", "article", "section") or any(r.startswith("gram.synth") for r in x["rule_ids"]):
        continue
    acc = x["accession_number"]
    if acc not in cache:
        cache = {acc: read_submission(paths[acc])}
    raw = cache[acc][x["head_raw_start"]:x["head_raw_end"]]
    txt = re.sub(r"<[^>]+>", "", raw); txt = re.sub(r"&#?\w+;", " ", txt); txt = re.sub(r"\s+", "", txt)
    lab = x["label_canon"].split()[1]
    romans = ["I","II","III","IV","V","VI","VII","VIII","IX","X","XI","XII","XIII","XIV","XV","XVI"]
    if x["level_kind"] == "section":
        parts = lab.split(".")
        alt = r"\.".join(f"0?{int(q)}" for q in parts)
    elif x["level_kind"] == "article" and lab.isdigit() and int(lab) <= 16:
        alt = f"(?:{lab}|{romans[int(lab)-1]}|{romans[int(lab)-1].lower()}|[A-Z]+)"
    else:
        alt = {"I": "(?:I|1)", "II": "(?:II|2)", "III": "(?:III|3)", "IV": "(?:IV|4)", "1": "(?:1|I)"}.get(lab, re.escape(lab))
    ok = re.search(r"(?i)(?:(item|part|article|section)s?\W{0,8}(no\.?)?|§)?" + alt, txt[:60]) is not None
    n += 1
    if not ok:
        bad += 1
        if bad <= 8:
            print("MISS", acc, x["label_canon"], x["head_raw_start"], repr(txt[:100]))
print(f"checked {n} heading nodes, {bad} misses")
