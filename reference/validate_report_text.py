"""Reference validation for the report-text call (RefCheck report stage).

Deterministic code only. It checks one model reply against the input that
produced it. It never reads or changes the verdict.

Usage:
    python validate_report_text.py <input.json> <reply.json>
Exit code 0 = valid, 1 = invalid (errors printed).
"""
import json
import re
import sys

FIELDS = ("error_type", "claim_marked", "evidence_passage", "evidence_context", "observation")
APPROVED_TYPES = frozenset({"source_says_opposite", "study_did_not_do_that",
    "data_mismatch", "inflated_figure", "figure_out_of_context", "measure_confused",
    "overstated_certainty", "source_does_not_say"})
MARK = re.compile(r"\*\*([\s\S]+?)\*\*")
NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _norm(s):
    return s.replace("\r\n", "\n").replace("\r", "\n")


def _check_marks(text, name, errors):
    if text.count("**") % 2:
        errors.append(f"{name}: unbalanced ** marks")
        return
    spans = MARK.findall(text)
    if len(spans)*2 != text.count("**"):
        errors.append(f"{name}: malformed or overlapping markers")
    if not 1 <= len(spans) <= 3:
        errors.append(f"{name}: expected 1 to 3 marked parts, found {len(spans)}")
    if any(not s.strip() for s in spans):
        errors.append(f"{name}: empty marked part")


def validate(inp, reply):
    errors = []
    # Validate the request contract before trusting any response. Unsupported
    # source types and malformed evidence must fail closed, not crash.
    if not isinstance(inp, dict):
        return ["input must be a JSON object"]
    if inp.get("report_language") != "en":
        errors.append("report_language must be en")
    if inp.get("citation_context") not in ("sole", "one_of_several"):
        errors.append("invalid citation_context")
    if inp.get("source_type") not in ("abstract", "full_text"):
        errors.append("source_type must be abstract or full_text")
    if not isinstance(inp.get("claim"), str) or not inp["claim"].strip():
        errors.append("claim must be nonempty string")
    if not isinstance(inp.get("marker"), str) or not inp["marker"].strip():
        errors.append("marker must be nonempty string")
    ev = inp.get("evidence")
    if (not isinstance(ev, list) or not ev
        or any(not isinstance(b, dict) or b.get("location") not in ("abstract", "body")
               or not isinstance(b.get("text"),str) or not b["text"].strip() for b in ev)):
        errors.append("evidence must be nonempty list of {location: abstract|body, text: nonempty string}")
    cats = inp.get("error_types")
    if (not isinstance(cats,list) or len(cats)!=8 or
        any(not isinstance(t,dict) or not isinstance(t.get("key"),str) for t in cats) or
        len({t['key'] for t in cats if isinstance(t,dict) and isinstance(t.get('key'),str)})!=8 or
        {t["key"] for t in cats if isinstance(t,dict) and isinstance(t.get("key"),str)} != APPROVED_TYPES):
        errors.append("error_types must contain exactly the eight distinct content category keys")
    for name in ("judge_quotes", "judge_explanations"):
        if not isinstance(inp.get(name),list) or any(not isinstance(v,str) for v in inp.get(name,[]) if isinstance(inp.get(name),list)):
            errors.append(f"{name} must be array of strings")
    if not errors and inp["source_type"] == "abstract" and any(b["location"] != "abstract" for b in ev):
        errors.append("abstract source_type cannot have body evidence")
    if errors:
        return errors
    # The judge inputs themselves must have verified quote provenance.
    evidence_full = "\n".join(b["text"] for b in ev)
    for quote in inp["judge_quotes"]:
        if quote and not any(_norm(quote) in _norm(b["text"]) for b in ev):
            errors.append("judge_quote is not literal in supplied evidence")
    if not isinstance(reply, dict) or set(reply) != set(FIELDS):
        return [f"reply must have exactly the fields {FIELDS}"]

    allowed = {t["key"] for t in inp["error_types"]}
    if not isinstance(reply["error_type"], str) or reply["error_type"] not in allowed:
        errors.append(f"error_type not in catalog: {reply['error_type']!r}")

    cm = reply["claim_marked"]
    if not isinstance(cm, str):
        errors.append("claim_marked must be a string")
    else:
        _check_marks(cm, "claim_marked", errors)
        if _norm(cm.replace("**", "")) != _norm(inp["claim"]):
            errors.append("claim_marked without ** is not identical to claim")
        marker = inp.get("marker")
        if marker and any(marker in s for s in MARK.findall(cm)):
            errors.append("claim_marked marks the citation marker")

    ep = reply["evidence_passage"]
    if not isinstance(ep, str):
        errors.append("evidence_passage must be a string")
    else:
        _check_marks(ep, "evidence_passage", errors)
        plain = _norm(ep.replace("**", ""))
        if not plain.strip() or not any(plain in _norm(b["text"]) for b in inp["evidence"]):
            errors.append("evidence_passage without ** is not a substring of one evidence text")
        if len(plain.split()) > 160:
            errors.append("evidence_passage longer than 160 words")

    ctx = reply["evidence_context"]
    if inp["source_type"] == "full_text":
        if ctx is not None:
            errors.append("evidence_context must be null when source_type is full_text")
    else:
        if not isinstance(ctx, str) or not ctx.strip():
            errors.append("evidence_context must be a non-empty string when source_type is abstract")
        elif "**" in ctx:
            errors.append("evidence_context must not contain ** marks")

    obs = reply["observation"]
    if not isinstance(obs, str) or not obs.strip():
        errors.append("observation must be a non-empty string")
    elif "**" in obs:
        errors.append("observation must not contain ** marks")

    # Numeric guard: every number written in observation or evidence_context
    # must appear in the evidence or in the claim. This catches invented
    # figures. It cannot detect invented non-numeric statements; those are
    # checked by human review of the first live outputs.
    allowed_text = _norm(" ".join(b["text"] for b in inp["evidence"]) + " " + inp["claim"])
    allowed_nums = set(NUM.findall(allowed_text))
    for name in ("observation", "evidence_context"):
        val = reply.get(name)
        if isinstance(val, str):
            extra = sorted(set(NUM.findall(val)) - allowed_nums)
            if extra:
                errors.append(f"{name} contains numbers not present in evidence or claim: {extra}")

    return errors


def report_text_status(inp, reply):
    """Return 'report_text_invalid' or 'ready'. Never a judge verdict."""
    if validate(inp, reply):
        return "report_text_invalid"
    return "ready"


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as f:
        inp = json.load(f)
    with open(sys.argv[2], encoding="utf-8") as f:
        reply = json.load(f)
    errs = validate(inp, reply)
    for e in errs:
        print("INVALID:", e)
    print("VALID" if not errs else f"{len(errs)} error(s)")
    sys.exit(1 if errs else 0)
