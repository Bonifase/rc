"""Independent report-stage acceptance regressions (browser + Python, offline).
Usage: python reference/test_independent_acceptance.py
Does not make provider calls.
"""
from pathlib import Path
import base64,copy,json,re,subprocess,sys
from bs4 import BeautifulSoup
sys.path.insert(0,str(Path(__file__).resolve().parent))
from validate_render_data import validate_render_data,validate_html,CONTENT,METADATA
from validate_report_text import validate
BASE=Path(__file__).resolve().parents[1]
HTML=(BASE/'RefCheck_Report_Ultima_Plantilla.html').read_text('utf-8')
# Reduce browser IPC overhead; embedded PDF is verified byte-by-byte and via pdftotext separately.
BROWSER_HTML=re.sub(r'(<script id="report-pdf" type="application/octet-stream">).*?(</script>)',r'\1__REPORT_PDF_NOT_EMBEDDED__\2',HTML,flags=re.S)
soup=BeautifulSoup(HTML,'html.parser')
data=json.loads(soup.find('script',id='report-data').text)
cat=json.loads(soup.find('script',id='report-error-types').text)

def check(cond,message):
 if not cond: raise AssertionError(message)
 print('OK',message,flush=True)

def injected(d):
 # inject a JSON record, not arbitrary script; maintain user-facing core as shipped.
 def sub(m):return m.group(1)+json.dumps(d,ensure_ascii=False,separators=(',',':'))+m.group(3)
 return re.sub(r'(<script id="report-data" type="application/json">)(.*?)(</script>)',sub,BROWSER_HTML,flags=re.S)

count=0
check(not validate_html(HTML),'baseline output schema');count+=1
check(set(cat)==CONTENT|METADATA and len(CONTENT)==8 and len(METADATA)==4,'approved catalog 8+4');count+=1
check(len(data['cases'])==9,'sample report must have 9 legitimate findings');count+=1
check(all(c['error_type'] in cat and c['reference_id'] for c in data['cases']),'no legacy example');count+=1
check(data['lang']=='en' and 'lang="en"' in HTML,'English-only');count+=1
check('fabricated_reference' not in HTML,'obsolete category must not remain anywhere');count+=1
check('No matching record' not in HTML,'misleading generic placeholder must not remain');count+=1
for mutation,description in [
    (lambda d:d.update(lang='es'),'Spanish report'),
    (lambda d:d['cases'][0].pop('reference_id'),'missing reference id'),
    (lambda d:d['cases'][0].update(error_type='fabricated_reference'),'legacy category'),
    (lambda d:d.update(references_reviewed=0),'understated reviewed count'),
    (lambda d:d.update(references_reviewed=None),'absent reviewed count'),
    (lambda d:d.update(error_types={'invented':{}}),'catalog override')]:
 d=copy.deepcopy(data); mutation(d)
 check(validate_render_data(d,cat),description+' must be rejected');count+=1

# English-only checker MUST reject Spanish at report-text stage as well.
for f in ('case_02','synthetic_abstract'):
 inp=json.loads((BASE/'fixtures'/f'{f}_input.json').read_text())
 reply=json.loads((BASE/'fixtures'/f'{f}_mock_reply.json').read_text())
 check(not validate(inp,reply),'valid report text example '+f);count+=1
 inp['report_language']='es'
 check(validate(inp,reply),'Spanish report-text must be rejected '+f);count+=1

# Ensure embedded PDF matches the active HTML sample, not the old sample.
pdf_script=soup.find('script',id='report-pdf').text.strip()
pdf=base64.b64decode(pdf_script,validate=True)
check(pdf.startswith(b'%PDF-'),'embedded PDF magic');count+=1
pdfpath=BASE/'reference'/'_acceptance_pdf_tmp.pdf';pdfpath.write_bytes(pdf)
try:
 txt=subprocess.run(['pdftotext',str(pdfpath),'-'],capture_output=True,text=True,check=True).stdout
 check('Nakamura' not in txt and 'Fabricated reference' not in txt,'embedded PDF must not retain removed reference');count+=1
 check('Bury' in txt and 'Corticosteroid' in txt,'PDF must contain accepted report example');count+=1
finally: pdfpath.unlink(missing_ok=True)

# Runtime-independent JavaScript checks on the exact tally function.
result=subprocess.run(['node',str(BASE/'reference'/'test_template_counts.cjs')],capture_output=True,text=True)
check(result.returncode==0,'actual HTML tally passes JavaScript regression');count+=1
# Parse and syntax check both inline executable scripts without changing them.
rawscripts=re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>',HTML,flags=re.S)
# Exclude JSON/data blocks and enormous embedded PDF via tag-based detection.
executables=re.findall(r'<script>(.*?)</script>',HTML,flags=re.S)
check(len(executables)==2,'two expected inline executable scripts');count+=1
for i,js in enumerate(executables):
    f=BASE/'reference'/f'_check_script_{i}.js'
    f.write_text(js,'utf-8')
    try:
        r=subprocess.run(['node','--check',str(f)],capture_output=True,text=True)
        check(r.returncode==0,'inline JavaScript syntax '+str(i)+': '+r.stderr[:150]);count+=1
    finally:f.unlink(missing_ok=True)
check('The declared DOI identifies a different article.' in HTML,'DOI placeholder identifies actual problem');count+=1
check('No corresponding entry in the bibliography.' in HTML,'missing-bibliography placeholder is accurate');count+=1
check('No matching record' not in HTML,'generic unproven nonmatch removed');count+=1
print(f'PASS: {count} independent checks, including negative-input scenarios, embedded PDF and actual JS validation')
