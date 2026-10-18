"""Executable report-stage acceptance test using synthetic inputs, no provider/network.
It uses the actual report-text validator, metadata mapper and HTML/JS template,
then opens the resulting report in Chromium, tests interactions, prints a PDF,
and checks the regenerated PDF appears inside the downloadable HTML.
No claim of a live Bonifase pipeline or Luna model test is made.
"""
import base64
import copy
import json
import re
import subprocess
import tempfile
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from metadata_status_mapping import build_metadata_plan
from validate_report_text import validate
from validate_render_data import validate_render_data

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / 'RefCheck_Report_Ultima_Plantilla.html').read_text('utf-8')
SAMPLE = BeautifulSoup(TEMPLATE,'html.parser')
CATALOG = json.loads(SAMPLE.find('script',id='report-error-types').text)

def load(name): return json.loads((ROOT/'fixtures'/name).read_text('utf-8'))

def json_script(obj):
    # Prevent user/source text from closing the JSON data element and executing script.
    return json.dumps(obj, ensure_ascii=False, separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')

def inject(data, pdf=None):
    assert not validate_render_data(data,CATALOG),validate_render_data(data,CATALOG)
    s=re.sub(r'(<script id="report-data" type="application/json">).*?(</script>)',
        lambda m:m.group(1)+json_script(data)+m.group(2),TEMPLATE,flags=re.S)
    blob=base64.b64encode(pdf).decode('ascii') if pdf else '__REPORT_PDF_NOT_EMBEDDED__'
    s=re.sub(r'(<script id="report-pdf" type="application/octet-stream">).*?(</script>)',
        lambda m:m.group(1)+blob+m.group(2),s,flags=re.S)
    return s

def f(ch,field,old=None,new=None,rid=1):
    return {'check_id':ch,'field':field,'reference_id':rid,'declared':old,'actual':new,'message':'synthetic test'}

raw = {
    'bibliography_status':[
        {'reference_id':1,'status':'miscited','usable_identifier':'10.1234/ref1','findings':[
            f(10,'journal','Wrong Journal','Correct Journal'),f(15,'retraction',None,'10.1234/retraction') ]},
        {'reference_id':2,'status':'wrong_article','usable_identifier':'10.1234/other','findings':[]},
        {'reference_id':3,'status':'not_found','usable_identifier':None,'findings':[]},
        {'reference_id':4,'status':'well_cited','usable_identifier':'10.1038/s41598-018-36681-w','findings':[]},
    ],
    'document_level_findings':[
        {'check_id':13,'reference_id':None,'field':'citation_marker','declared':'17','actual':None,'message':'synthetic orphan'},
    ], 'not_evaluated_checks':[]
}
plan=build_metadata_plan(raw,{1:'10.1234/ref1',2:'10.1234/other',3:None,4:'10.1038/s41598-018-36681-w'})
assert plan['references_reviewed']==3
assert [d.show_content for d in plan['decisions']]==[True,False,False,True]
assert [c.error_type for d in plan['decisions'] for c in d.cards]==['wrong_reference_details','retracted_article','doi_leads_elsewhere']
assert len(plan['orphan_cards'])==1

# Simulate exactly one finalized unsupported pair on ref 4, and no content
# for a wrong-article reference. This mock is the actual existing case_02 fixture.
inp=load('case_02_input.json');reply=load('case_02_mock_reply.json')
assert not validate(inp,reply)
cl=reply['claim_marked'].replace('[2]','[4]')
assert cl.replace('**','')==inp['claim'].replace('[2]','[4]')
content={'reference_id':4,'kind':'content','marker':'[4]', 'source_label':'Faught & Vijayan (2018)',
 'error_type':reply['error_type'],'claim':cl,'evidence':reply['evidence_passage'],
 'observation':reply['observation'],'passage_origin':'full_text',
 'reference':'Faught & Vijayan (2018), Scientific Reports.','doi':'10.1038/s41598-018-36681-w'}

# The actual mapper provides decisions; bibliographic prose fields are added
# from mock declared and official records as specified in REPORT_STAGE_SPEC.
cards=[content]
for d in plan['decisions']:
 for card in d.cards:
    base={'reference_id':d.reference_id,'kind':'metadata','marker':f'[{d.reference_id}]',
     'source_label': f'Synthetic reference {d.reference_id}',
     'error_type':card.error_type,'claim':f'Synthetic sentence citing [{d.reference_id}].',
     'reference_as_generated': 'B. Writer (2026). Sample. **Wrong Journal**. doi: 10.1234/ref1',
     'doi': '10.1234/ref1','findings':[f['message'] for f in card.findings]}
    if card.error_type=='wrong_reference_details':
      base['reference_official']='B. Writer (2026). Sample. **Correct Journal**. doi: 10.1234/ref1'
    elif card.error_type=='retracted_article':
      base['reference_official']='B. Writer (2026). Sample. Correct Journal. doi: 10.1234/ref1'
    elif card.error_type=='doi_leads_elsewhere':
      base['observation']='The declared DOI identifies a different article.';base['doi_dead']=True;base['doi']='10.1234/other';base['reference_as_generated']='C. Writer. Wrong DOI 10.1234/other';base['reference_official']=None
    cards.append(base)
for num, findings in plan['orphan_cards']:
 cards.append({'reference_id':'orphan:'+num,'kind':'metadata','marker':'['+num+']',
     'source_label':'Not in the reference list','error_type':'citation_without_reference',
     'claim':'Synthetic sentence with missing citation ['+num+'].','count_as_reference':False,
     'reference_as_generated': '**['+num+']** is not in the reference list.',
     'reference_official':None,'findings':[f'[{num}] is cited without a bibliography entry.']})

report={'schema_version':'2','lang':'en','title':'Synthetic offline end-to-end acceptance',
  'manuscript_file':'OFFLINE_SYNTHETIC.docx','references_reviewed':plan['references_reviewed'],
  'report_complete':True,'cases':cards,'pdf_filename':'OFFLINE_SYNTHETIC.pdf'}
assert len(cards)==5
assert not validate_render_data(report,CATALOG), validate_render_data(report,CATALOG)

# Invalid structures should return diagnostics, NEVER TypeError.
for invalid in ([],{},None,True):
    bad=copy.deepcopy(report);bad['cases'][0]['error_type']=invalid
    try:errs=validate_render_data(bad,CATALOG)
    except Exception as e:raise AssertionError('Validation crashed for '+repr(invalid)) from e
    assert errs
# A content error must never be excluded from the distinct reference count.
invalid_count=copy.deepcopy(report)
invalid_count['cases'][0]['count_as_reference']=False
assert validate_render_data(invalid_count,CATALOG)
invalid_ids=copy.deepcopy(report)
invalid_ids['cases'][0]['id']='duplicate'
invalid_ids['cases'][1]['id']='duplicate'
assert validate_render_data(invalid_ids,CATALOG)
for invalid in ('not an object',[],None):
    cc=copy.deepcopy(CATALOG);cc['source_says_opposite']['label']=invalid
    assert validate_render_data(report,cc)

# HTML script-termination and link/attribute injection regression tests.
mal=copy.deepcopy(report)
mal['title']='A title </script><script>window.__injected = true</script>'
mal['cases'][0]['id']='x" autofocus onfocus="window.__attribute_injected = true'
mal['cases'][0]['source_url']='javascript:alert(1)'
mal['cases'][0].pop('doi')
assert not validate_render_data(mal,CATALOG)
html = inject(report)
html_mal = inject(mal)
assert '<script>window.__injected' not in html_mal

with sync_playwright() as w:
  browser=w.chromium.launch(executable_path='/usr/bin/chromium',headless=True,
      args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
  print('before page',flush=True)
  page=browser.new_page(viewport={'width':1200,'height':960})
  print('after page',flush=True)
  page.route('**/*',lambda r:r.abort() if r.request.resource_type in ('stylesheet','font','image') else r.continue_())
  errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  print('before html set',flush=True)
  page.set_content(html,wait_until='domcontentloaded',timeout=12000)
  print('after html set',flush=True)
  assert not errors,errors
  assert page.locator('article.case').count()==5
  assert page.locator('#n-content').inner_text()=='1'
  assert page.locator('#n-meta').inner_text()=='2' # ref 1 twice + ref2, orphan excluded
  assert '3 references were reviewed. 3 contain errors.' in page.locator('#verdict').inner_text()
  assert page.locator('.case-head').count()==5
  page.locator('.case-head').first.click()
  assert page.locator('.case-head').first.get_attribute('aria-expanded')=='true'
  assert not page.locator('.case-body').first.is_hidden()
  page.locator('.stat[data-filter="metadata"]').click()
  assert page.locator('article.case[data-kind="content"]').first.is_hidden()
  page.locator('#clear-filter').click()
  assert not page.locator('article.case[data-kind="content"]').first.is_hidden()
  # Exercise hostile user data before printing (post-print browser IPC can
  # stall on this hosted Chromium build, so PDF is the final browser action).
  print('before hostile',flush=True)
  hostile=browser.new_page(viewport={'width':1200,'height':960})
  print('after hostile',flush=True)
  hostile.route('**/*',lambda r:r.abort() if r.request.resource_type in ('stylesheet','font','image') else r.continue_())
  hostile_errors=[];hostile.on('pageerror',lambda e:hostile_errors.append(str(e)))
  hostile.set_content(html_mal,wait_until='domcontentloaded',timeout=30000)
  assert not hostile.evaluate('Boolean(window.__injected || window.__attribute_injected)')
  assert hostile.locator('[onfocus], [onclick]').count()==0
  assert hostile.locator('a[href^="javascript:"]').count()==0
  assert hostile.locator('article.case').count()==5
  assert not hostile_errors,hostile_errors
  page.screenshot(path=str(ROOT/'offline_end_to_end.png'),full_page=False,timeout=12000)
  print('PASS: Chromium UI and malicious-input tests',flush=True)
  page.pdf(path=str(ROOT/'offline_end_to_end.pdf'),format='A4',print_background=True)
  print('PASS: Chromium PDF export',flush=True)
  browser.close()

# The final HTML must carry a freshly generated downloadable PDF, not sample one.
pdf=(ROOT/'offline_end_to_end.pdf').read_bytes()
assert pdf.startswith(b'%PDF-')
final_html=inject(report,pdf)
(ROOT/'offline_end_to_end.html').write_text(final_html,'utf-8')
payload=re.search(r'<script id="report-pdf" type="application/octet-stream">(.*?)</script>', final_html,re.S).group(1)
assert base64.b64decode(payload,validate=True)==pdf
assert json.loads(re.search(r'<script id="report-data" type="application/json">(.*?)</script>',final_html,re.S).group(1))==report
print('PASS: downloadable PDF bytes embedded accurately',flush=True)

# Extract text independently to ensure PDF corresponds to this actual mock report.
res=subprocess.run(['pdftotext',str(ROOT/'offline_end_to_end.pdf'),'-'],capture_output=True,text=True,check=True)
assert 'Synthetic offline end-to-end acceptance' in res.stdout
assert 'The source says the opposite' in res.stdout
assert 'DOI leads to a different article' in res.stdout
assert 'Citation without a reference' in res.stdout
assert 'Retracted article' in res.stdout
assert 'Nakamura' not in res.stdout
print('PASS: synthetic metadata->classification + validated content->HTML->Chromium interactions->fresh PDF->HTML download')
print('PASS: 5 cards; 3 distinct affected refs; 3 reviewed; 1 content; 2 metadata refs; orphan excluded from counts')
print('PASS: validation rejects malformed types; injected scripts, malicious attributes and javascript: links are inert')
print('PASS: embedded downloadable PDF regenerated from the same report data')
