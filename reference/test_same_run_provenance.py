"""Exercise resolved actual objects -> source records -> cards -> final gate, no network."""
from types import SimpleNamespace as S
from pathlib import Path
import json,copy,hashlib
from bs4 import BeautifulSoup
from official_provenance import records_from_resolution
from render_metadata_cards import build_cards_from_metadata
from validate_render_data import validate_for_publication

root=Path(__file__).resolve().parents[1]
soup=BeautifulSoup((root/'RefCheck_Report_Ultima_Plantilla.html').read_text(),'html.parser')
cat=json.loads(soup.find('script',id='report-error-types').text)

def D(n,raw,doi,title,first):
    return S(reference_id=n,raw=raw,doi=doi,title=title,authors=[S(family=first,given='Jane')])
def A(doi,title,first,year=2020):
    return S(doi=doi,title=title,year=year,journal='Journal of Evidence',volume='3',issue='1',pages='11-22',authors=[S(family=first,given='Jane')])
def finding(n,id,field,d=None,a=None):
    return {'reference_id':n,'check_id':id,'field':field,'declared':d,'actual':a,'message':'resolved comparison'}
refs=[
    D(1,'Smith, J. (2020). Actual Study. Wrong Journal, 3, 11-22. https://doi.org/10.1/a','10.1/a','Actual Study','Smith'),
    D(2,'Jones, J. (2018). Wrong DOI. Journal X. https://doi.org/10.1/bad','10.1/bad','Wrong DOI','Jones'),
    D(3,'Baker, J. (2019). Unindexed Article. Local J.','', 'Unindexed Article','Baker'),
    D(4,'Lee, J. (2021). Retraction Study. Journal of Evidence. https://doi.org/10.1/ret','10.1/ret','Retraction Study','Lee'),
]
resolved={
    1:S(status='_needs_field_comparison',actual=A('10.1/a','Actual Study','Smith')),
    2:S(status='wrong_article',actual=None),
    3:S(status='not_found',actual=None),
    4:S(status='_needs_field_comparison',actual=A('10.1/ret','Retraction Study','Lee',2021))
}
provenance=records_from_resolution(refs,resolved,lambda a,b: 1.0 if a==b else 0.2)
assert provenance['1']['verified_first_author_match']
assert provenance['2']['verified_wrong_doi']
raw={'bibliography_status':[
 {'reference_id':1,'status':'miscited','usable_identifier':'10.1/a','findings':[finding(1,10,'journal','Wrong Journal','Journal of Evidence')]},
 {'reference_id':2,'status':'wrong_article','usable_identifier':'10.1/bad','findings':[]},
 {'reference_id':3,'status':'not_found','usable_identifier':None,'findings':[]},
 {'reference_id':4,'status':'miscited','usable_identifier':'10.1/ret','findings':[finding(4,15,'retraction',None,'10.1/notice')]},
 ],'document_level_findings':[finding(None,13,'citation_marker','17',None)],
 'not_evaluated_checks':[], 'official_records':provenance}
man={'bibliography':[{'reference_id':r.reference_id,'marker':f'[{r.reference_id}]','reference':r.raw,'doi':r.doi,'source_label':r.authors[0].family} for r in refs],
 'citations':[{'reference_id':r.reference_id,'marker':f'[{r.reference_id}]','claim':f'A factual claim citing [{r.reference_id}].'} for r in refs],
 'orphan_citations':[{'marker':'[17]','claim':'An orphan citation [17].'}],
 'references_reviewed':3}
result=build_cards_from_metadata(raw,man)
assert not result['blocks_final']
assert result['references_reviewed']==3
assert [c['error_type'] for c in result['cards']]==['wrong_reference_details','doi_leads_elsewhere','retracted_article','citation_without_reference']
report={'lang':'en','report_complete':True,'references_reviewed':3,'cases':result['cards']}
frozen_hash=hashlib.sha256(json.dumps(man,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf8')).hexdigest()
errs=validate_for_publication(report,cat,man,provenance,frozen_hash)
assert not errs,errs

# Same-run provenance must be authoritative, not hand-supplied text. Test false
# source identity, forged official text, unrelated claims and invalid markers.
for mutation in ('first_author','title','official','marker','claim','missing_actual','bad_retraction'):
    rp=copy.deepcopy(raw)
    nm=copy.deepcopy(man)
    pv=copy.deepcopy(provenance)
    if mutation=='first_author': pv['1']['verified_first_author_match']=False
    if mutation=='title': pv['1']['verified_title_match']=False
    if mutation=='official': pv['1']['official_ref_text']='Fabricated corrected citation.'
    if mutation=='marker': nm['citations'][0]['marker']='[999]'
    if mutation=='claim': nm['citations'][0]['claim']='An unrelated sentence.'
    if mutation=='missing_actual': del pv['1']
    if mutation=='bad_retraction':pv['4']['verified_first_author_match']=False
    rp['official_records']=pv
    try:
        out=build_cards_from_metadata(rp,nm)
        rep={'lang':'en','report_complete':True,'references_reviewed':out['references_reviewed'],'cases':out['cards']}
        errors=validate_for_publication(rep,cat,nm,pv,frozen_hash)
        if mutation in ('first_author','title','bad_retraction'):
            # Unverified identity is recorded only internally; not shown.
            assert len(out['cards'])<len(result['cards'])
        else:
            assert errors, mutation+' silently accepted'
    except ValueError:
        pass
print('PASS: resolved records -> 4 metadata card types -> validated FINAL, with 7 adverse provenance tests')

# Wrong declared DOI that was rescued by the same paper's title and author:
# generated entry shows the incorrect DOI, clickable link must target the
# resolved article, never the unrelated DOI in the manuscript.
rescue_refs=[D(6,'Smith, J. (2020). Actual Study. Journal. https://doi.org/10.1/wrong',
               '10.1/wrong','Actual Study','Smith')]
rescued_records=records_from_resolution(rescue_refs,
    {6:S(status='_needs_field_comparison', actual=A('10.1/correct','Actual Study','Smith'))},
    lambda x,y:1.0 if x==y else 0.2)
rescue_output={'bibliography_status':[{'reference_id':6,'status':'miscited',
    'usable_identifier':'10.1/correct','findings':[]}],
    'document_level_findings':[],'not_evaluated_checks':[],
    'official_records':rescued_records}
rescue_manifest={'bibliography':[{'reference_id':6,'marker':'[6]',
    'reference':rescue_refs[0].raw,'doi':'10.1/wrong'}],
    'citations':[{'reference_id':6,'marker':'[6]','claim':'Something [6].'}],
    'orphan_citations':[],'references_reviewed':1}
rescue=build_cards_from_metadata(rescue_output,rescue_manifest)
assert len(rescue['cards'])==1
assert rescue['cards'][0]['doi']=='10.1/correct',rescue
assert '10.1/wrong' in rescue['cards'][0]['reference_as_generated'],rescue
print('PASS: rescued DOI never used as link to unrelated paper')
