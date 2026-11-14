import asyncio
import hashlib
import io
import json
import time
import re
import zipfile
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape

import httpx
from docx import Document
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pypdf import PdfReader
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from starlette.concurrency import run_in_threadpool

from matching import DEMO, compare, query_phrases, stream, tokens
from providers import MODES, detect, request, rewrite, search_adapters, setting, source

ROOT = Path(__file__).parent
load_dotenv(ROOT/'.env')
SIZE = 10*1024*1024
MAX_CHARS = 600000
LANG = {'auto','en','bn','hi','es','fr','ar'}
history = OrderedDict()
cache = OrderedDict()
outbound = asyncio.Semaphore(5)
scans = asyncio.Semaphore(2)

def enabled(name):
    return setting(name).lower() == 'true'

@asynccontextmanager
async def lifespan(app):
    async with httpx.AsyncClient(timeout=10,trust_env=False,headers={'User-Agent':'TextGuard/1.0 (text comparison application)'}) as client:
        app.state.client = client
        yield

app = FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
origins = [x.strip() for x in setting('TRUSTED_ORIGINS').split(',') if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_methods=['GET','POST'],allow_headers=['Content-Type'])

@app.middleware('http')
async def protect(req, call_next):
    if enabled('REQUIRE_HTTPS') and req.url.scheme != 'https':
        return JSONResponse({'detail':'Use the HTTPS address.'},400)
    if req.method == 'POST':
        origin = req.headers.get('origin')
        if origin and origin not in [str(req.base_url).rstrip('/'), *origins]:
            return JSONResponse({'detail':'This origin is not allowed.'},403)
        if req.url.path.startswith('/api/'):
            length = req.headers.get('content-length','')
            if not length.isdecimal():
                return JSONResponse({'detail':'Content-Length is required.'},411)
            if int(length)>SIZE+65536:
                return JSONResponse({'detail':'Request is too large.'},413)
            ip = req.client.host if req.client else 'unknown'
            now = time.monotonic()
            queue = history.setdefault(ip,deque())
            history.move_to_end(ip)
            if len(history)>4096:
                history.popitem(last=False)
            while queue and now-queue[0]>=60:
                queue.popleft()
            if len(queue)>=20:
                return JSONResponse({'detail':'Rate limit reached. Try again shortly.'},429,headers={'Retry-After':'60'})
            queue.append(now)
    response = await call_next(req)
    response.headers.update({'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
                             'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                             'Permissions-Policy':'camera=(), microphone=(), geolocation=()'})
    if req.url.path.startswith('/api/'):
        response.headers['Cache-Control']='no-store'
    if enabled('REQUIRE_HTTPS'):
        response.headers['Strict-Transport-Security']='max-age=31536000'
    return response

class Reference(BaseModel):
    title: str = Field(max_length=150)
    text: str = Field(max_length=MAX_CHARS)

class Scan(BaseModel):
    text: str = Field(max_length=MAX_CHARS)
    language: str = 'auto'
    external: bool = False
    exclusions: list[str] = Field(default_factory=list,max_length=3)
    references: list[Reference] = Field(default_factory=list,max_length=5)
    mode: str = 'Standard'
    strength: int = Field(default=2,ge=1,le=3)
    variant: int = Field(default=0,ge=0,le=10000)

def validate(text):
    if not text.strip() or not tokens(text):
        raise HTTPException(422,'Enter text containing words.')
    if len(text)>MAX_CHARS or len(tokens(text))>25000:
        raise HTTPException(422,'Use at most 25,000 words and 600,000 characters.')
    return text

def extract(name, data):
    path = Path(name).suffix.lower()
    file = io.BytesIO(data)
    if path == '.txt':
        try:
            return data.decode('utf-8-sig')
        except UnicodeDecodeError:
            if data[:2] in (b'\xff\xfe',b'\xfe\xff'):
                return data.decode('utf-16')
            raise
    if path == '.docx':
        with zipfile.ZipFile(file) as archive:
            entries = archive.infolist()
            if len(entries)>2000 or sum(x.file_size for x in entries)>40*1024*1024:
                raise ValueError('Archive expansion limit')
        doc = Document(file)
        return '\n'.join(['\n'.join(c.text for row in item.rows for c in row.cells) if hasattr(item,'rows') else item.text for item in doc.iter_inner_content()])
    if path == '.pdf':
        reader = PdfReader(file)
        if reader.is_encrypted or len(reader.pages)>300:
            raise ValueError('Protected or oversized PDF')
        parts = []
        remaining = MAX_CHARS
        for page in reader.pages:
            part = page.extract_text() or ''
            remaining -= len(part)
            if remaining < 0:
                raise ValueError('Extracted text limit')
            parts.append(part)
        return '\n'.join(parts)
    raise HTTPException(415,'Upload a TXT, DOCX, or PDF file.')

@app.post('/api/extract')
async def upload(file: UploadFile = File(...)):
    data = await file.read(SIZE+1)
    await file.close()
    if len(data)>SIZE:
        raise HTTPException(413,'Files must be 10 MB or smaller.')
    try:
        text = await run_in_threadpool(extract,file.filename or '',data)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422,'Could not extract text. Use a readable UTF-8 TXT, DOCX, or unlocked PDF.')
    if not text.strip():
        raise HTTPException(422,'No selectable text found. Run OCR on scanned PDFs first.')
    return {'text':validate(text)}

@app.get('/api/config')
def config():
    return {'detector':bool(setting('GPTZERO_API_KEY')),'rewrite':bool(setting('OPENAI_API_KEY')),
            'search':enabled('ENABLE_PUBLIC_SEARCH'),'commercial':bool(setting('COMMERCIAL_SCAN_URL')),'sample':DEMO}

@app.post('/api/detect')
async def detection(body:Scan):
    return await detect(app.state.client,validate(body.text),body.external)

@app.post('/api/rewrite')
async def paraphrase(body:Scan):
    if body.mode not in MODES or body.language not in LANG:
        raise HTTPException(422,'Choose a supported writing mode and language.')
    return await rewrite(app.state.client,validate(body.text),body.mode,body.strength,body.language,body.external,body.variant)

def event(stage, **data):
    return json.dumps({'stage':stage,**data},ensure_ascii=False)+'\n'

async def search(provider, adapter, phrase, language):
    key = hashlib.sha256((provider+'\0'+language+'\0'+phrase).encode()).digest()
    item = cache.get(key)
    if item and item[0]>time.monotonic():
        cache.move_to_end(key)
        return item[1], 'cached'
    try:
        async with outbound:
            results = await adapter(app.state.client,phrase,language)
    except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError):
        return [], 'unavailable'
    cache[key] = (time.monotonic()+300,results)
    if len(cache)>128:
        cache.popitem(last=False)
    return results,'queried'

async def commercial(text, language):
    address = setting('COMMERCIAL_SCAN_URL')
    if urlparse(address).scheme != 'https' or not setting('COMMERCIAL_SCAN_TOKEN'):
        return [],'unavailable'
    try:
        result = await request(app.state.client,'POST',address,
                               headers={'Authorization':'Bearer '+setting('COMMERCIAL_SCAN_TOKEN')},
                               json={'text':text,'language':language})
        return [source(x['title'],x['url'],'Commercial excerpt',x['text']) for x in result['sources'][:10]],'queried'
    except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError):
        return [],'unavailable'

@app.post('/api/similarity')
async def similarity(body:Scan):
    validate(body.text)
    if body.language not in LANG:
        raise HTTPException(422,'Choose a supported language.')
    for ref in body.references:
        validate(ref.text)
    async def scan():
        async with scans:
            groups = list(stream(body.text,body.exclusions))
            phrases = query_phrases(groups)
            use_search = body.external and enabled('ENABLE_PUBLIC_SEARCH')
            use_commercial = body.external and bool(setting('COMMERCIAL_SCAN_URL')) and bool(groups)
            adapters = search_adapters() if use_search and phrases else {}
            sources = [dict(title=x.title,url='',type='Private reference',text=x.text) for x in body.references]
            queried = {'Private references': ['compared']} if sources else {}
            if not use_search and not use_commercial and not sources:
                sources = [dict(title='TextGuard supercapacitor sample',url='',type='Built-in demo text',text=DEMO)]
                queried['Built-in demo text'] = ['compared']
            jobs = {}
            for name, adapter in adapters.items():
                async def run_provider(label, adapter):
                    batches = await asyncio.gather(*(search(label,adapter,phrase,body.language) for phrase in phrases))
                    return label,batches
                jobs[name] = asyncio.create_task(run_provider(name,adapter))
            if use_commercial:
                async def run_commercial():
                    text = '\n'.join(body.text[group[0].start():group[-1].end()] for group in groups)
                    return 'Commercial adapter',[await commercial(text,body.language)]
                jobs['Commercial adapter'] = asyncio.create_task(run_commercial())
            yield event('Preparing comparison',progress=8)
            try:
                for i, job in enumerate(asyncio.as_completed(jobs.values()),1):
                    name,batches = await job
                    for found,status in batches:
                        sources.extend(found)
                        queried.setdefault(name,[]).append(status)
                    yield event('Finished '+name,progress=10+int(62*i/max(1,len(jobs))))
                unique = {}
                for item in sources:
                    key = (item['url'] or item['title'],item['type'])
                    if key not in unique:
                        unique[key] = item
                    elif item['text'] not in unique[key]['text']:
                        unique[key]['text'] = (unique[key]['text']+'\n'+item['text'])[:30000]
                yield event('Matching passages',progress=82)
                result = await run_in_threadpool(compare,groups,list(unique.values()))
                yield event('Complete',progress=100,result={**result,'queried':queried,'sampled_phrases':len(phrases) if adapters else 0,
                    'compared_sources':len(unique),'timestamp':datetime.now(timezone.utc).isoformat()})
            finally:
                for job in jobs.values():
                    if not job.done():
                        job.cancel()
                await asyncio.gather(*jobs.values(),return_exceptions=True)
    return StreamingResponse(scan(),media_type='application/x-ndjson',headers={'X-Accel-Buffering':'no'})

class Export(BaseModel):
    text: str = Field(max_length=MAX_CHARS)
    lines: list[str] = Field(default_factory=list,max_length=300)
    spans: list[list[int]] = Field(default_factory=list,max_length=30000)

@app.post('/api/docx')
def docx_export(body: Export):
    doc = Document()
    for line in body.text.splitlines():
        doc.add_paragraph(line)
    output = io.BytesIO()
    doc.save(output)
    return Response(output.getvalue(),media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers={'Content-Disposition':'attachment; filename="textguard-rewrite.docx"'})

PDF_FONTS = ('NotoSans','NotoSansBengali','NotoSansDevanagari','NotoSansArabic')
for font in PDF_FONTS:
    pdfmetrics.registerFont(TTFont(font,ROOT/'fonts'/f'{font}-Regular.ttf'))

def pdf_markup(value):
    runs = re.findall(r'[\u0980-\u09ff]+|[\u0900-\u097f]+|[\u0600-\u06ff]+|[^\u0980-\u09ff\u0900-\u097f\u0600-\u06ff]+', value)
    markup = []
    for run in runs:
        first = ord(run[0])
        name = ('NotoSansBengali' if 0x0980<=first<=0x09ff else
                'NotoSansDevanagari' if 0x0900<=first<=0x097f else
                'NotoSansArabic' if 0x0600<=first<=0x06ff else 'NotoSans')
        markup.append(f'<font name="{name}">{escape(run)}</font>')
    return ''.join(markup)

@app.post('/api/report')
def pdf_export(body: Export):
    target = io.BytesIO()
    style = getSampleStyleSheet()
    for name in ('Normal','BodyText'):
        style[name].wordWrap = 'CJK'
    content = [Paragraph('TextGuard analysis report',style['Title'])]
    for line in body.lines:
        content.append(Paragraph(pdf_markup(line[:1000]),style['BodyText']))
    content.append(Spacer(1,12))
    colors_by_kind = {0:'#f9df8b',1:'#c9f1da',2:'#f9df8b',3:'#ffcace'}
    spans = sorted(x for x in body.spans if len(x)==3 and 0<=x[0]<x[1]<=len(body.text) and x[2] in colors_by_kind)
    cursor = 0
    pieces = []
    for a,b,kind in spans:
        if a < cursor:
            continue
        pieces.append(pdf_markup(body.text[cursor:a]))
        pieces.append('<font backColor="'+colors_by_kind[kind]+'">'+pdf_markup(body.text[a:b])+'</font>')
        cursor = b
    pieces.append(pdf_markup(body.text[cursor:]))
    # Each paragraph is independently paginated by ReportLab.
    markup = ''.join(pieces).replace('\r','').replace('\n','<br/>')
    if markup:
        content.append(Paragraph(markup,style['Normal']))
    SimpleDocTemplate(target).build(content)
    return Response(target.getvalue(),media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="textguard-report.pdf"'})

app.mount('/',StaticFiles(directory=ROOT/'static',html=True),name='ui')
