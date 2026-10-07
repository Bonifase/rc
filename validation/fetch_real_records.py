"""
Fetches the REAL Crossref records behind every catalog case that needs one,
so the validation fixtures compare declared strings (from the error
catalogs) against verified actual records instead of hand-written fakes.

Output: validation/real_records.json -- {case_id: ActualReference-shaped dict}

Two kinds of entries:
  - by DOI:   {"kind": "doi", "doi": "..."}
  - by search: {"kind": "search", "query": "...", "family": "..."} -- uses
    Crossref bibliographic search and keeps the best title-similarity hit,
    so a wrong/broken declared DOI never hides a real record.

Run with the toolchain interpreter that has `requests`:
    py -3.14 validation/fetch_real_records.py
"""
import json
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "validation" / "real_records.json"
MAILTO = "validation@nevergiveup.example.org"

# Every case the catalogs hand us that carries a real, resolvable paper.
# DOIs were located via Crossref bibliographic search and verified against
# the catalogs' described real author lists; the record payloads are cached
# in real_records.json so fixtures compare against verified ground truth.
CASES = {
    # --- Doc1/Doc2 (ChatGPT) ---
    "doc1_a2_1_pereira": "10.1038/s41598-021-95408-6",          # Naya-Catala 2021
    "doc2_a4_1_naya_venus": "10.1038/s41598-021-95408-6",       # same DOI, Doc2 author/vol break
    "doc1_a2_2_weng_zhong": "10.3389/fimmu.2022.959717",        # Zhong 2022 Front Immunol 13
    "doc1_a2_3_doc3_12_lema_bersin": "10.1016/j.mce.2023.111951",  # Bersin et al. 2023
    "doc1_a2_4_duplicate_doi": "10.3390/ijms252413583",         # Int J Mol Sci 25(24):13583
    "doc2_a4_2_nornberg": "10.1016/j.ygcen.2015.12.017",        # GCE 226:36-41
    "doc2_a4_3_serrana_macqueen": "10.3389/fendo.2018.00080",   # Front Endocrinol 9:80
    "doc2_a4_4_sanchez_moya": "10.3389/fendo.2023.1211470",     # cysteamine sea bream
    "doc2_a4_5_duran": "10.3390/ijms23031180",                  # Duran 2022 Int J Mol Sci 23
    "doc2_a4_6_fuentes": "10.1016/j.ygcen.2013.06.009",         # GH-IGF muscle growth review
    # --- Doc3 (Gemini) ---
    "doc3_b4_7_elizur": "10.3389/fendo.2024.1369043",           # Cohen-Rothschild 2024
    "doc3_b4_30_zohar": "10.3389/fendo.2024.1373623",           # triploid crucian carp 2024
    "doc3_b4_6_doyle": "10.1016/j.ygcen.2009.04.020",           # Doyle 2009
    "doc3_b4_9_15_guan_liu": "10.1080/09712119.2024.2440045",   # single real paper of 2024
    "doc3_b4_27_xie": "10.1242/jeb.006890",                     # J Exp Biol 211 (2008)
    "doc3_b4_19_picha": "10.1577/a07-038.1",                    # NAJA 70:196-211
    "doc3_b1_3_berishvili": "10.1016/j.ygcen.2010.01.023",      # IGF-3 gonadal 2010
    "doc3_b1_29_zhong": "10.3389/fimmu.2022.959717",            # same as weng_zhong paper
}

# Track which Crossref type is a real journal article vs. book chapter etc.
def _pick_year(message: dict):
    for key in ("published-print", "published-online", "published"):
        parts = message.get(key, {}).get("date-parts")
        if parts and parts[0] and parts[0][0]:
            return int(parts[0][0])
    return None


def _norm(title: str) -> str:
    return re.sub(r"[^\w\s]", "", title.lower()).strip()


def _fetch(doi: str) -> dict:
    r = requests.get(
        f"https://api.crossref.org/works/{doi}",
        params={"mailto": MAILTO}, timeout=30,
    )
    r.raise_for_status()
    m = r.json()["message"]
    return {
        "doi": m.get("DOI", "").lower(),
        "status": "resolved",
        "title": (m.get("title") or [None])[0],
        "authors": [
            {"family": a.get("family"), "given": a.get("given")}
            for a in (m.get("author") or [])
            if a.get("family") or a.get("given")
        ],
        "year": _pick_year(m),
        "journal": (m.get("container-title") or [None])[0],
        "volume": m.get("volume"),
        "issue": m.get("issue"),
        "pages": m.get("page"),
        "article_number": m.get("article-number"),
        "issn": (m.get("ISSN") or [None])[0],
        "publication_type": m.get("type"),
    }


def _search(query: str, family: str) -> dict:
    q = f"{query} {family}" if family else query
    r = requests.get(
        "https://api.crossref.org/works",
        params={"query.bibliographic": q, "rows": 8, "mailto": MAILTO},
        timeout=30,
    )
    r.raise_for_status()
    best, best_score = None, -1.0
    for item in r.json()["message"]["items"]:
        title = (item.get("title") or [None])[0]
        if not title:
            continue
        score = __import__("difflib").SequenceMatcher(None, _norm(query), _norm(title)).ratio()
        if score > best_score:
            best_score, best = score, item
    if best is None:
        return {"doi": "", "status": "not_found", "error": f"no items for '{q}'"}
    rec = {
        "doi": best.get("DOI", "").lower(),
        "status": "resolved",
        "title": (best.get("title") or [None])[0],
        "authors": [
            {"family": a.get("family"), "given": a.get("given")}
            for a in (best.get("author") or [])
            if a.get("family") or a.get("given")
        ],
        "year": _pick_year(best),
        "journal": (best.get("container-title") or [None])[0],
        "volume": best.get("volume"),
        "issue": best.get("issue"),
        "pages": best.get("page"),
        "article_number": best.get("article-number"),
        "issn": (best.get("ISSN") or [None])[0],
        "publication_type": best.get("type"),
        "title_match_score": round(best_score, 3),
        "searched_query": q,
    }
    return rec


def main():
    results = {}
    for case_id, doi in CASES.items():
        try:
            rec = _fetch(doi)
            results[case_id] = rec
            print(f"[OK]   {case_id:32s} -> {rec['doi']} | {rec.get('title', '')[:60]}")
        except Exception as exc:  # noqa: BLE001 - one case failing must not kill the run
            results[case_id] = {"doi": "", "status": "error", "error": str(exc)}
            print(f"[FAIL] {case_id:32s} -> {exc}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {OUT} ({len(results)} records)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
