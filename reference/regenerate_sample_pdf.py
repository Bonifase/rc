"""Update the downloadable sample PDF to match the sample HTML, without network calls."""
from pathlib import Path
from playwright.sync_api import sync_playwright
import re,base64,subprocess,tempfile
p=Path(__file__).resolve().parents[1]/'RefCheck_Report_Ultima_Plantilla.html'
s=p.read_text('utf-8')
pdf_block=r'(<script id="report-pdf" type="application/octet-stream">).*?(</script>)'
assert len(re.findall(pdf_block,s,re.S))==1
printable=re.sub(pdf_block,r'\1__REPORT_PDF_NOT_EMBEDDED__\2',s,flags=re.S)
with sync_playwright() as w:
 b=w.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
 page=b.new_page(viewport={'width':1200,'height':960})
 page.route('**/*',lambda r:r.abort() if r.request.resource_type in ('stylesheet','font','image') else r.continue_())
 errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.set_content(printable,wait_until='domcontentloaded',timeout=20000)
 assert page.locator('article.case').count()==9
 assert not errors,errors
 pdf=page.pdf(format='A4',print_background=True)
 b.close()
assert pdf.startswith(b'%PDF-')
with tempfile.NamedTemporaryFile(suffix='.pdf') as f:
 f.write(pdf);f.flush()
 contents=subprocess.run(['pdftotext',f.name,'-'],capture_output=True,text=True,check=True).stdout
 assert 'Each displayed content finding' in contents
 assert 'Fabricated reference' not in contents
assert len(re.findall(pdf_block,s,re.S))==1
new=re.sub(pdf_block,lambda m:m.group(1)+base64.b64encode(pdf).decode('ascii')+m.group(2),s,flags=re.S)
p.write_text(new,'utf-8')
print('PASS: sample PDF regenerated from current HTML, 9 cases, updated method wording, no removed category')
