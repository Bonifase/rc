"""Independent negative and positive tests for corrected publication gate."""
import copy
import json
import hashlib
from pathlib import Path
from bs4 import BeautifulSoup
from validate_render_data import validate_render_data, validate_for_publication

ROOT=Path(__file__).resolve().parents[1]
soup=BeautifulSoup((ROOT/'RefCheck_Report_Ultima_Plantilla.html').read_text(),'html.parser')
data=json.loads(soup.find('script',id='report-data').text)
catalog=json.loads(soup.find('script',id='report-error-types').text)

assert validate_render_data(data,catalog)==[], validate_render_data(data,catalog)
content=next(c for c in data['cases'] if c['kind']=='content')
metadata=next(c for c in data['cases'] if c['kind']=='metadata')
for field in ('claim','evidence','observation','source_label','reference'):
    copydata=copy.deepcopy(data); case=next(c for c in copydata['cases'] if c['kind']=='content');case[field]=''
    assert validate_render_data(copydata,catalog), 'accepted empty content '+field
for field in ('claim','source_label','reference_as_generated','findings'):
    copydata=copy.deepcopy(data);case=next(c for c in copydata['cases'] if c['kind']=='metadata');case[field]=[] if field=='findings' else ''
    assert validate_render_data(copydata,catalog), 'accepted empty metadata '+field
for marker in (False, None, 0, 'false'):
    copydata=copy.deepcopy(data);copydata['report_complete']=marker
    assert validate_render_data(copydata,catalog), 'accepted incomplete report '+repr(marker)
assert validate_for_publication(data,catalog,None,{},"f"*64)
assert validate_for_publication(data,catalog,{},None,"f"*64)

# Construct a small independently supplied 2-case manifest and same-run
# official record; no test relies on the sample's existing metadata counts.
cc=copy.deepcopy(content);cc['reference_id']=5;cc['marker']='[5]';cc['claim']=cc['claim'].replace('[2]','[5]')
mm=copy.deepcopy(metadata);mm['reference_id']=6;mm['marker']='[6]';mm['claim']='Statement citing [6].';mm['reference_as_generated']='Smith (2020). An inaccurate citation.'
mm['reference_official']='**Jones, J. (2021). Correct source. Journal X. https://doi.org/10.1/jones**'
report={'lang':'en','report_complete':True,'cases':[cc,mm], 'references_reviewed':2}
manifest={'bibliography':[
    {'reference_id':5,'marker':'[5]','reference':'Source citation 5'},
    {'reference_id':6,'marker':'[6]','reference':'Smith (2020). An inaccurate citation.'}],
    'citations':[{'reference_id':5,'marker':'[5]','claim':cc['claim'].replace('**','')},
                 {'reference_id':6,'marker':'[6]','claim':mm['claim']}],
    'orphan_citations':[],'references_reviewed':2}
official={'6':{'verified_title_match':True,'verified_first_author_match':True,
               'official_ref_text':'Jones, J. (2021). Correct source. Journal X. https://doi.org/10.1/jones',
 'actual_fields':{'doi':'10.1/jones','title':'Correct source','authors':[{'family':'Jones','given':'J'}],
 'year':2021,'journal':'Journal X','volume':None,'issue':None,'pages':None}}}
fixed_hash=hashlib.sha256(json.dumps(manifest,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf8')).hexdigest()
assert validate_for_publication(report,catalog,manifest,official,fixed_hash)==[], validate_for_publication(report,catalog,manifest,official,fixed_hash)
for change,desc in [
    (lambda d:d['cases'][0].update(marker='[999]'),'wrong marker'),
    (lambda d:d['cases'][1].update(reference_id=99),'wrong id'),
    (lambda d:d['cases'][1].update(reference_official='Someone else'),'fake official'),
    (lambda d:d.update(references_reviewed=42),'fake reviewed total')]:
    bad=copy.deepcopy(report);change(bad)
    assert validate_for_publication(bad,catalog,manifest,official,fixed_hash),desc
for change,desc in [
    (lambda r:r['6'].update(verified_title_match=False),'false title match'),
    (lambda r:r['6'].update(verified_first_author_match=False),'false first-author match'),
    (lambda r:r.pop('6'),'missing official source')]:
    bad=copy.deepcopy(official);change(bad)
    assert validate_for_publication(report,catalog,manifest,bad,fixed_hash),desc
print('PASS 22 publication gate probes: required fields, completeness, exact manuscript provenance and official record')
