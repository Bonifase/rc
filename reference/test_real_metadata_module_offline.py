"""Run Bonifase's actual metadata checks + patched report module with network stubbed.
Only the tenacity HTTP-retry decorator is stubbed because the container lacks
that package; no external service is contacted. Metadata checks are original.
"""
import sys,types,importlib.util,os
from pathlib import Path
root=Path(__file__).resolve().parents[1]
repo=Path(os.environ.get('REFCHECK_REPO_ROOT',str(root.parent))).resolve()
if not (repo/'src/metadata/report.py').is_file():
    raise RuntimeError('Set REFCHECK_REPO_ROOT to the directory containing Bonifase src/metadata/report.py')
sys.path.insert(0,str(repo));sys.path.insert(0,str(root))
# Minimal stubs of unavailable HTTP retry combinators; fake resolution avoids HTTP.
t=types.ModuleType('tenacity')
t.retry=lambda *a,**kw:(lambda f:f)
t.stop_after_attempt=lambda *a,**kw:None
t.wait_exponential=lambda *a,**kw:None
t.retry_if_exception_type=lambda *a,**kw:None
sys.modules['tenacity']=t
from src.metadata.document import MetadataDoc
from src.metadata.extraction import DeclaredReference,Author
from src.metadata.crossref_metadata import ActualReference
from src.metadata.resolution import ResolvedReference

spec=importlib.util.spec_from_file_location('src.metadata.report_candidate',root/'integration_candidate/report.py')
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)

r=DeclaredReference(reference_id=1,raw='Smith, J. (2020). Study. Other Journal, 2, 9-10. https://doi.org/10.1234/test',
    authors=[Author(family='Smith',given='J')],year=2020,title='Study',journal='Other Journal',
    volume='2',issue=None,pages='9-10',doi='10.1234/test',issn=None)
actual=ActualReference(doi='10.1234/test',status='resolved',title='Study',authors=[Author(family='Smith',given='J')],
    year=2020,journal='Journal of Evidence',volume='2',pages='9-10',issn=None)
m.resolve_reference=lambda ref,contact_email=None:ResolvedReference(ref.reference_id,'_needs_field_comparison',actual=actual,usable_identifier=actual.doi)
doc=MetadataDoc(sentences=[{'id':'s1','text':'This is a stated finding [1].'}],body_text='This is a stated finding [1].')
output=m.build_report(doc,[r])
assert output['bibliography_status'][0]['status']=='miscited',output
assert output['official_records']['1']['verified_first_author_match'],output
assert output['official_records']['1']['verified_title_match'],output
assert 'Journal of Evidence' in output['official_records']['1']['official_ref_text']
from render_metadata_cards import build_cards_from_metadata
manifest={'bibliography':[{'reference_id':1,'marker':'[1]','reference':r.raw,'doi':r.doi,'source_label':'Smith (2020)'}],
 'citations':[{'reference_id':1,'marker':'[1]','claim':doc.sentences[0]['text']}],
 'orphan_citations':[],'references_reviewed':1}
plan=build_cards_from_metadata(output,manifest)
assert plan['references_reviewed']==1,plan
assert len(plan['cards'])==1 and plan['cards'][0]['error_type']=='wrong_reference_details',plan
from bs4 import BeautifulSoup
import json,hashlib
from validate_render_data import validate_for_publication
soup=BeautifulSoup((root/'RefCheck_Report_Ultima_Plantilla.html').read_text('utf-8'),'html.parser')
catalog=json.loads(soup.find('script',id='report-error-types').text)
report={'lang':'en','report_complete':True,'references_reviewed':1,'cases':plan['cards']}
sha=hashlib.sha256(json.dumps(manifest,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
errors=validate_for_publication(report,catalog,manifest,output['official_records'],sha)
assert not errors,errors
print('PASS: original Bonifase metadata checks -> patched build_report -> same-run source -> card -> FINAL gate')

# Render the SAME output, without rewriting/hand-entering the source data.
import re,base64
from playwright.sync_api import sync_playwright
source=(root/'RefCheck_Report_Ultima_Plantilla.html').read_text('utf-8')
cleanjson=json.dumps(report,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e')
rendered=re.sub(r'(<script id="report-data" type="application/json">).*?(</script>)',
    lambda mt:mt.group(1)+cleanjson+mt.group(2),source,flags=re.S)
rendered=re.sub(r'(<script id="report-pdf" type="application/octet-stream">).*?(</script>)',
    r'\1__REPORT_PDF_NOT_EMBEDDED__\2',rendered,flags=re.S)
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,
        args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
    page=browser.new_page(viewport={'width':1200,'height':900})
    page.route('**/*',lambda rt:rt.abort() if rt.request.resource_type in ('stylesheet','font','image') else rt.continue_())
    js_errors=[];page.on('pageerror',lambda e:js_errors.append(str(e)))
    page.set_content(rendered,wait_until='domcontentloaded',timeout=15000)
    assert not js_errors,js_errors
    assert page.locator('article.case').count()==1
    assert page.locator('#n-meta').inner_text()=='1'
    assert '1 reference was reviewed.' in page.locator('#verdict').inner_text(),page.locator('#verdict').inner_text()
    page.locator('.case-head').first.click()
    assert 'Journal of Evidence' in page.locator('.case-body').first.inner_text()
    pdf=page.pdf(format='A4',print_background=True)
    assert pdf.startswith(b'%PDF-')
    browser.close()
print('PASS: same original metadata output -> interactive Chromium report -> generated PDF')
