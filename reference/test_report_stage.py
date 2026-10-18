"""Offline regression tests for report-stage source contracts."""
import json
import pathlib
import unittest
from validate_report_text import validate, report_text_status
from metadata_status_mapping import classify_reference, orphan_citation_cards, build_metadata_plan
ROOT = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
def load(n): return json.loads((ROOT/n).read_text(encoding="utf-8"))
def entry(status="well_cited", findings=None, reference_id=1):
    return {"reference_id": reference_id, "status": status, "findings": findings or [],
            "blocks_phase_2": status in {"wrong_article", "not_found", "technical_failure"}}
def finding(check_id, field, declared="old", actual="new", reference_id=1):
    return {"check_id":check_id,"field":field,"declared":declared,"actual":actual,
            "reference_id":reference_id,"severity":"warning","message":"fixture"}


class TextTests(unittest.TestCase):
 def test_valid_original_fixtures(self):
    for name in ("case_02", "synthetic_abstract"):
        with self.subTest(name=name):
            inp=load(f"{name}_input.json"); reply=load(f"{name}_mock_reply.json")
            self.assertEqual(validate(inp,reply),[])
            self.assertEqual(report_text_status(inp,reply),"ready")
 def test_invalid_original_fixtures(self):
    for name in ("case_02", "synthetic_abstract"):
        with self.subTest(name=name):
            self.assertTrue(validate(load(f"{name}_input.json"),load(f"{name}_invalid_reply.json")))
 def test_invalid_error_types_rejected_without_exception(self):
    inp=load("case_02_input.json");reply=load("case_02_mock_reply.json")
    for bad in (None,"unknown_9th",["source_says_opposite"],{"k":1},3,True):
        with self.subTest(bad=bad):
            r=dict(reply); r["error_type"]=bad
            self.assertEqual(report_text_status(inp,r),"report_text_invalid")
 def test_two_new_content_types_accepted(self):
    inp=load("case_02_input.json");reply=load("case_02_mock_reply.json")
    for t in ("source_does_not_say","overstated_certainty"):
        r=dict(reply); r["error_type"]=t
        self.assertEqual(validate(inp,r),[])
 def test_no_invented_numbers(self):
    inp=load("case_02_input.json");reply=load("case_02_mock_reply.json")
    reply["observation"]+=" Random 9876543."
    self.assertTrue(validate(inp,reply))
 def test_bad_request_contract_and_forged_quote_rejected(self):
    inp=load("case_02_input.json");reply=load("case_02_mock_reply.json")
    inp["source_type"]="unknown";self.assertTrue(validate(inp,reply))
    inp=load("case_02_input.json");inp["judge_quotes"]=["This quote is made up"]
    self.assertTrue(any("judge_quote" in x for x in validate(inp,reply)))
    inp=load("case_02_input.json");inp["evidence"]=[{"location":"snippet","text":"fake"}]
    self.assertTrue(validate(inp,reply))
 def test_unsupported_catalog_in_input_rejected(self):
    inp=load("case_02_input.json"); reply=load("case_02_mock_reply.json")
    inp["error_types"][0]["key"]="fake_category"
    self.assertTrue(validate(inp,reply))
 def test_source_type_and_location_inconsistent(self):
    inp=load("synthetic_abstract_input.json");reply=load("synthetic_abstract_mock_reply.json")
    inp["evidence"][0]["location"]="body"
    self.assertTrue(validate(inp,reply))
 def test_malformed_input_rejected(self):
    self.assertTrue(validate({}, {}))

def types(d): return [c.error_type for c in d.cards]

class MetadataTests(unittest.TestCase):
 def test_direct_field_mismatch_shown_automatically(self):
    for cid, field in ((9,"title"),(10,"journal"),(10,"volume"),(10,"pages"),(16,"year"),(14,"authors")):
        with self.subTest(cid=cid, field=field):
            d=classify_reference(entry("miscited",[finding(cid,field)]))
            self.assertEqual(types(d),["wrong_reference_details"]); self.assertTrue(d.reviewed and d.show_content)
 def test_template_case_1(self):
    d=classify_reference(entry("miscited",[finding(10,"journal","Mol Cell Endocrinol","Gen Comp Endocrinol"),
        finding(16,"year","2008","2007"),finding(10,"volume","289","153")]))
    self.assertEqual(types(d),["wrong_reference_details"]); self.assertEqual(len(d.cards[0].findings),3)
 def test_well_cited_author_omission_shown(self):
    self.assertEqual(types(classify_reference(entry("well_cited",[finding(14,"authors","2","4")]))),["wrong_reference_details"])
 def test_retraction_shown_as_own_card(self):
    d=classify_reference(entry("miscited",[finding(15,"retraction",None,"10.1/notice")]))
    self.assertEqual(types(d),["retracted_article"]); self.assertFalse(d.internal_findings)
 def test_retraction_and_field_error_two_cards_one_reference(self):
    d=classify_reference(entry("miscited",[finding(10,"journal"),finding(15,"retraction",None,"n")]))
    self.assertEqual(types(d),["wrong_reference_details","retracted_article"])
 def test_wrong_article_shows_doi_card_no_content(self):
    e=entry("wrong_article"); e["usable_identifier"]="10.1/x"
    d=classify_reference(e)
    self.assertEqual(types(d),["doi_leads_elsewhere"]); self.assertTrue(d.reviewed); self.assertFalse(d.show_content)
    self.assertIn("10.1/x", d.cards[0].findings[0]["message"])
 def test_rescued_wrong_doi_not_lost(self):
    e=entry("well_cited"); e["usable_identifier"]="10.1/right"
    d=classify_reference(e, declared_doi="https://doi.org/10.1/WRONG")
    self.assertEqual(types(d),["wrong_reference_details"]); self.assertEqual(d.cards[0].findings[0]["field"],"doi")
    e2=entry("well_cited"); e2["usable_identifier"]="10.1/Same"
    self.assertEqual(types(classify_reference(e2, declared_doi="doi:10.1/same")),[])
 def test_not_found_synthesized_technical_internal_only_not_reviewed(self):
    for st in ("not_found","synthesized_title","technical_failure"):
        with self.subTest(st=st):
            d=classify_reference(entry(st,[finding(10,"journal")]))
            self.assertEqual(types(d),[]); self.assertFalse(d.reviewed); self.assertFalse(d.show_content)
            self.assertFalse(d.blocks_final); self.assertEqual(len(d.internal_findings),1)
 def test_identity_doubt_hides_everything_for_that_reference(self):
    d=classify_reference(entry("miscited",[finding(10,"journal"),finding(15,"retraction",None,"n"),finding(7,"author","smith","jones")]))
    self.assertEqual(types(d),[]); self.assertFalse(d.show_content); self.assertFalse(d.reviewed)
    self.assertEqual(len(d.internal_findings),3)
 def test_heuristics_internal_only(self):
    for check,field,actual in ((1,"doi",None),(4,"value","30%"),(5,"volume","12"),(8,"author","x"),(11,"volume","~3-5"),(13,"citation_marker",None),(14,"authors",None)):
        with self.subTest(check=check):
            d=classify_reference(entry("miscited",[finding(check,field,"old",actual)]))
            self.assertEqual(types(d),[]); self.assertTrue(d.show_content); self.assertEqual(len(d.internal_findings),1)
 def test_unknown_status_blocks_final(self):
    self.assertTrue(classify_reference(entry("mystery")).blocks_final)
 def test_orphan_cards_one_per_number_others_internal(self):
    orphan=lambda n:{"check_id":13,"reference_id":None,"field":"citation_marker","declared":str(n),"actual":None,"message":"m"}
    cards,internal=orphan_citation_cards([orphan(17),orphan(17),orphan(18),
        {"check_id":2,"reference_id":None,"field":"citation_count","declared":"5","actual":"4","message":"m"}])
    self.assertEqual([n for n,_ in cards],["17","18"]); self.assertEqual(len(cards[0][1]),2); self.assertEqual(len(internal),1)
 def test_plan_counts_only_reviewed_references(self):
    e3=entry("wrong_article",reference_id=3); e3["usable_identifier"]="10.1/z"
    m={"bibliography_status":[entry("well_cited",reference_id=1),entry("not_found",reference_id=2),e3,
        entry("technical_failure",reference_id=4)],"document_level_findings":[],"not_evaluated_checks":[]}
    plan=build_metadata_plan(m)
    self.assertEqual(plan["references_reviewed"],2); self.assertEqual(plan["blocks_final"],[])
 def test_plan_rejects_duplicate_or_missing_ids(self):
    with self.assertRaises(ValueError): build_metadata_plan({"bibliography_status":[entry(),entry()]})
    with self.assertRaises(ValueError): build_metadata_plan({"bibliography_status":[entry(reference_id=None)]})

if __name__ == "__main__": unittest.main()
