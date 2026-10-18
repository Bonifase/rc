"""Fail-closed offline validation of report JSON and approved label catalogue.

The integration must call validate_render_data before publishing a FINAL report.
"""
import json
import re
import hashlib
from pathlib import Path

CONTENT = frozenset({
    'source_says_opposite', 'study_did_not_do_that', 'data_mismatch',
    'inflated_figure', 'figure_out_of_context', 'measure_confused',
    'source_does_not_say', 'overstated_certainty'})
METADATA = frozenset({
    'wrong_reference_details', 'retracted_article',
    'doi_leads_elsewhere', 'citation_without_reference'})


def validate_render_data(data, catalog, manifest=None):
    errors = []
    if not isinstance(data, dict) or not isinstance(catalog, dict):
        return ['report data and catalog must be JSON objects']
    if data.get('report_complete') is not True:
        errors.append('report_complete must be true to publish FINAL')
    if data.get('lang') != 'en':
        errors.append('report language must be en')
    if 'error_types' in data:
        errors.append('report data must not override the frozen catalog')
    if set(catalog) != CONTENT | METADATA:
        errors.append('catalog must contain exactly 8 content and 4 metadata categories')
    else:
        for key in CONTENT | METADATA:
            c = catalog[key]
            label = c.get('label') if isinstance(c, dict) else None
            if (not isinstance(c, dict) or c.get('kind') != ('content' if key in CONTENT else 'metadata')
                or not isinstance(label, dict) or not isinstance(label.get('en'), str)
                or not label['en'].strip()):
                errors.append('invalid catalog label/kind for '+key)
    cases = data.get('cases')
    if not isinstance(cases, list):
        return errors + ['cases must be an array']
    reviewed = data.get('references_reviewed')
    if type(reviewed) is not int or reviewed < 0:
        errors.append('references_reviewed must be a nonnegative integer')
    affected = set()
    card_ids = set()
    for i, c in enumerate(cases):
        pre=f'cases[{i}]'
        if not isinstance(c,dict):
            errors.append(pre+': must be an object');continue
        card_id=c.get('id')
        if card_id is not None:
            if not isinstance(card_id,(str,int)) or isinstance(card_id,bool) or not str(card_id).strip():
                errors.append(pre+': invalid card id')
            elif str(card_id) in card_ids:
                errors.append(pre+': duplicate card id')
            else:card_ids.add(str(card_id))
        rid=c.get('reference_id')
        if rid is None or not isinstance(rid,(str,int)) or not str(rid).strip():
            errors.append(pre+': missing reference_id')
        cat=c.get('error_type')
        kind=c.get('kind')
        if kind == 'content':
            if not isinstance(cat,str) or cat not in CONTENT:errors.append(pre+': invalid content category')
            for field in ('claim','evidence','observation','source_label','reference'):
                if not isinstance(c.get(field), str) or not c[field].strip():
                    errors.append(pre+': missing '+field)
            if c.get('passage_origin') not in ('abstract','full_text'):
                errors.append(pre+': missing or invalid passage_origin')
        elif kind == 'metadata':
            if not isinstance(cat,str) or cat not in METADATA:errors.append(pre+': invalid metadata category')
            for field in ('claim','source_label','reference_as_generated'):
                if not isinstance(c.get(field), str) or not c[field].strip():
                    errors.append(pre+': missing '+field)
            f = c.get('findings')
            if not isinstance(f,list) or not f or any(not isinstance(x,str) or not x.strip() for x in f):
                errors.append(pre+': missing or invalid findings')
            if cat in ('wrong_reference_details','retracted_article'):
                if not isinstance(c.get('reference_official'),str) or not c['reference_official'].strip():
                    errors.append(pre+': confirmed metadata requires an official record')
            if cat == 'doi_leads_elsewhere' and not c.get('observation'):
                errors.append(pre+': DOI mismatch requires observation')
            if cat == 'doi_leads_elsewhere':
                if c.get('doi_dead') is not True or c.get('reference_official'):
                    errors.append(pre+': wrong DOI must not link to a different article as official')
            if cat == 'citation_without_reference' and c.get('count_as_reference') is not False:
                errors.append(pre+': orphan citation must not count as bibliography entry')
        else:
            errors.append(pre+': unknown kind')
        if not isinstance(c.get('marker'),str) or not c['marker'].strip():
            errors.append(pre+': missing citation marker')
        if cat != 'citation_without_reference' and c.get('count_as_reference') is False:
            errors.append(pre+': only orphan citations may be excluded from reference counts')
        if rid is not None and c.get('count_as_reference') is not False:
            affected.add(str(rid).strip())
    if type(reviewed) is int and len(affected) > reviewed:
        errors.append('references_reviewed less than number of distinct affected references')
    if manifest is not None:
        errors.extend(validate_manifest_provenance(data,manifest))
    return errors


def validate_manifest_provenance(data, manifest):
    """Check every reported card against the frozen manuscript manifest.

    Manifest shape:
      {'bibliography': [{'reference_id': 1, 'marker': '[1]', 'reference': '...'}],
       'citations': [{'reference_id': 1, 'marker': '[1]', 'claim': '...'}],
       'orphan_citations': [{'marker': '[17]', 'claim': '...'}],
       'references_reviewed': 1}
    This is a REQUIRED second gate for production FINAL, not a substitute for
    the render-data structural gate. It cannot be replaced with synthetic IDs.
    """
    if not isinstance(data,dict) or not isinstance(manifest,dict):
        return ['report and manuscript manifest must be objects']
    errors=[]
    for name in ('bibliography','citations','orphan_citations'):
        if not isinstance(manifest.get(name),list) or any(not isinstance(x,dict) for x in manifest[name]):
            return [f'manifest.{name} must be an array of objects']
    if type(manifest.get('references_reviewed')) is not int:
        errors.append('manifest.references_reviewed missing')
    elif data.get('references_reviewed')!=manifest['references_reviewed']:
        errors.append('references_reviewed does not match independent provenance')
    refs={}
    for r in manifest['bibliography']:
        rid=str(r.get('reference_id'))
        if rid in refs: errors.append('duplicate manifest reference '+rid)
        refs[rid]=r
    citations={(str(c.get('reference_id')),c.get('marker'),c.get('claim')) for c in manifest['citations']}
    orphan={(o.get('marker'),o.get('claim')) for o in manifest['orphan_citations']}
    for i,c in enumerate(data.get('cases',[])):
        if not isinstance(c,dict):continue
        rid=str(c.get('reference_id'))
        marker=c.get('marker')
        claim=c.get('claim')
        if c.get('error_type')=='citation_without_reference':
            if not isinstance(marker,str) or rid != 'orphan:'+marker.strip('[]'):
                errors.append(f'cases[{i}]: orphan identity mismatch')
            if (marker,claim) not in orphan:
                errors.append(f'cases[{i}]: orphan marker/claim absent from manifest')
            if any(r.get('marker')==marker for r in refs.values()):
                errors.append(f'cases[{i}]: orphan is present in bibliography')
        else:
            ref=refs.get(rid)
            if ref is None or marker != ref.get('marker'):
                errors.append(f'cases[{i}]: reference ID/marker not in bibliography')
                continue
            if c.get('kind')=='metadata':
                if c.get('reference_as_generated','').replace('**','')!=ref.get('reference'):
                    errors.append(f'cases[{i}]: metadata original text mismatches bibliography')
                if (rid,marker,claim) not in citations:
                    errors.append(f'cases[{i}]: metadata citation not in manifest')
            elif c.get('kind')=='content':
                if (rid,marker,claim.replace('**','') if isinstance(claim,str) else claim) not in citations:
                    errors.append(f'cases[{i}]: content claim/marker not in manifest')
    return errors


def validate_for_publication(data, catalog, manifest, official_records, expected_manifest_sha256=None):
    """The mandatory, fail-closed FINAL gate; all provenance comes from actual upstream modules.

    official_records is a map keyed by reference_id containing a serializer of
    the actual Crossref record used during that SAME run; never re-query for a
    loosely matching paper or fabricate bibliographic fields.
    """
    errors=[]
    if manifest is None or official_records is None:
        return ['FINAL requires both frozen manuscript manifest and resolved Crossref records']
    if not isinstance(expected_manifest_sha256,str) or len(expected_manifest_sha256)!=64:
        return ['FINAL requires frozen intake manifest hash']
    actual_manifest_sha256=hashlib.sha256(json.dumps(manifest,sort_keys=True,ensure_ascii=False,
        separators=(',',':')).encode('utf-8')).hexdigest()
    if actual_manifest_sha256!=expected_manifest_sha256:
        return ['manuscript manifest differs from frozen intake hash']
    errors.extend(validate_render_data(data, catalog, manifest))
    if not isinstance(official_records,dict):
        return errors+['official_records must be a mapping']
    for i,c in enumerate(data.get('cases',[]) if isinstance(data,dict) and isinstance(data.get('cases'),list) else []):
        if not isinstance(c,dict) or c.get('kind')!='metadata': continue
        rid=str(c.get('reference_id'))
        typ=c.get('error_type')
        if typ in ('wrong_reference_details','retracted_article'):
            record=official_records.get(rid)
            if not isinstance(record,dict):
                errors.append(f'cases[{i}]: missing same-run Crossref record')
                continue
            if record.get('verified_title_match') is not True or record.get('verified_first_author_match') is not True:
                errors.append(f'cases[{i}]: cited-article identity not established')
            try:
                from official_provenance import format_official_reference
                reconstructed=format_official_reference(record.get('actual_fields'))
            except (TypeError,ValueError,KeyError):
                reconstructed=None
            if not reconstructed or reconstructed!=record.get('official_ref_text'):
                errors.append(f'cases[{i}]: Crossref record fields do not reproduce official citation')
            if (not isinstance(record.get('official_ref_text'),str) or
                not isinstance(c.get('reference_official'),str) or
                c['reference_official'].replace('**','')!=record['official_ref_text']):
                errors.append(f'cases[{i}]: official reference not identical to resolved record')
        if typ=='doi_leads_elsewhere':
            record=official_records.get(rid)
            if not isinstance(record,dict) or record.get('verified_wrong_doi') is not True:
                errors.append(f'cases[{i}]: wrong DOI not supported by resolver provenance')
    return errors


def validate_html(html):
    """Validate inline sample data without requiring BeautifulSoup."""
    def block(id):
        m = re.search(r'<script id="'+re.escape(id)+r'" type="application/json">(.*?)</script>',html,re.S)
        if not m: raise ValueError('Missing JSON script: '+id)
        return json.loads(m.group(1))
    return validate_render_data(block('report-data'), block('report-error-types'))


if __name__ == '__main__':
    import sys
    source = Path(sys.argv[1])
    errs = validate_html(source.read_text('utf-8'))
    if errs:
        for e in errs:print('FAIL:',e)
        sys.exit(1)
    print('PASS: report output contract, 8+4 categories, English, identities and counts')
