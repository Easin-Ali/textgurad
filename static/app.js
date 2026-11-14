'use strict';
const $ = id => document.getElementById(id);
const input = $('input');
const results = $('results');
const labels = {detect:'AI Detector',similarity:'Plagiarism',rewrite:'Paraphraser'};
const actions = {detect:'Analyze text',similarity:'Check similarity',rewrite:'Rewrite text'};
const words = text => (text.match(/(?:[\p{L}\p{N}]\p{M}*)+(?:[’'-](?:[\p{L}\p{N}]\p{M}*)+)*/gu) || []).length;
let current = 'detect', config = {}, references = [], controller, variant = 0, activeUploads = 0, generation = 0;
const local = {
  read(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } },
  write(key,value) { try { localStorage.setItem(key,JSON.stringify(value)); } catch { /* Storage is optional. */ } },
  remove(key) { try { localStorage.removeItem(key); } catch { /* Storage is optional. */ } }
};
let history = local.read('tg-history',[]);
function create(tag,text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function button(title, fn) {
  const b = create('button', title, 'secondary');
  b.type = 'button';
  b.addEventListener('click', async () => { try { await fn(); } catch (e) { if (e.name !== 'AbortError') error(e.message); } });
  return b;
}
function error(message) {
  $('error').textContent = message || '';
  $('error').hidden = !message;
}
function updateCount() {
  $('count').textContent = `${words(input.value).toLocaleString()} / 25,000 words · ${[...input.value].length.toLocaleString()} characters`;
}
function setText(text) {
  input.value = text;
  updateCount();
}
function select(tool) {
  current = tool;
  for (const tab of $('tabs').querySelectorAll('button')) tab.setAttribute('aria-pressed',String(tab.dataset.tool === tool));
  $('similarOptions').hidden = tool !== 'similarity';
  $('rewriteOptions').hidden = tool !== 'rewrite';
  $('run').textContent = actions[tool];
  const names = tool === 'detect' ? (config.detector ? 'GPTZero' : '') :
    tool === 'rewrite' ? (config.rewrite ? 'OpenAI' : '') :
    [config.search && 'Online search',config.commercial && 'Commercial adapter'].filter(Boolean).join(' · ');
  $('external').disabled = !names;
  $('availability').textContent = names ? `${names} configured` : 'Local mode available';
}
function showHistory() {
  $('history').replaceChildren();
  for (const item of history) {
    const line = create('li',`${item.label} · ${item.summary} · ${new Date(item.timestamp).toLocaleString()}`);
    const remove = button('Delete', () => {
      history = history.filter(entry => entry.id !== item.id);
      local.write('tg-history',history);
      showHistory();
    });
    remove.setAttribute('aria-label',`Delete ${item.label} result from ${new Date(item.timestamp).toLocaleString()}`);
    line.append(remove);
    $('history').append(line);
  }
  if (!history.length) $('history').append(create('li','No saved results.','small'));
}
function saveHistory(tool,summary,timestamp) {
  if (!$('remember').checked) return;
  history.unshift({id:crypto.randomUUID?.() || Date.now().toString(),label:labels[tool],summary,timestamp});
  history = history.slice(0,20);
  local.write('tg-history',history);
  showHistory();
}
async function call(url, data, signal) {
  const form = data instanceof FormData;
  const response = await fetch(url,{method:'POST',headers:form ? undefined : {'Content-Type':'application/json'},
    body:form ? data : JSON.stringify(data),signal});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status}). Check the input limits and try again.`);
  }
  return response;
}
function busy(label, percent) {
  $('progressBox').hidden = false;
  $('progressText').textContent = label;
  if (percent === undefined) $('progress').removeAttribute('value');
  else $('progress').value = percent;
}
function reportAction(text,lines,spans) {
  return button('Download PDF report',async () => {
    const response = await call('/api/report',{text,lines,spans});
    download(await response.blob(),'textguard-report.pdf');
  });
}
function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = create('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url),3000);
}
function passage(text, spans, demo=false) {
  const container = create('div',undefined,'passage');
  container.dir = 'auto';
  container.tabIndex = 0;
  let cursor = 0;
  const chars = [...text];
  for (const [a,b,kind] of spans) {
    if (a < cursor) continue;
    container.append(chars.slice(cursor,a).join(''));
    const mark = create('mark',chars.slice(a,b).join(''),kind);
    if (['human','uncertain','ai'].includes(kind)) {
      const name = (demo ? {human:'Demo low',uncertain:'Demo medium',ai:'Demo high'} : {human:'Likely human',uncertain:'Uncertain',ai:'Likely AI'})[kind];
      mark.title = name;
      mark.setAttribute('aria-label',name+': '+mark.textContent);
    }
    container.append(mark);
    cursor = b;
  }
  container.append(chars.slice(cursor).join(''));
  return container;
}
function gauge(score) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns,'svg');
  svg.setAttribute('class','gauge');
  svg.setAttribute('viewBox','0 0 100 100');
  svg.setAttribute('role','img');
  svg.setAttribute('aria-label',score+' percent');
  for (const amount of [null,score]) {
    const circle = document.createElementNS(ns,'circle');
    for (const [k,v] of Object.entries({cx:50,cy:50,r:43,fill:'none','stroke-width':7})) circle.setAttribute(k,v);
    if (amount !== null) {
      circle.setAttribute('pathLength','100');
      circle.setAttribute('stroke-dasharray',`${amount} 100`);
      circle.setAttribute('transform','rotate(-90 50 50)');
      if (!amount) circle.setAttribute('opacity','0');
    }
    svg.append(circle);
  }
  const label = document.createElementNS(ns,'text');
  label.setAttribute('x','50');
  label.setAttribute('y','57');
  label.setAttribute('text-anchor','middle');
  label.textContent = Math.round(score)+'%';
  svg.append(label);
  return svg;
}
function metric(name,value) {
  const item = create('div',name,'metric');
  item.append(create('strong',value));
  return item;
}
function detector(text,data,time) {
  const demo = data.mode.startsWith('Demo');
  const layout = create('div',undefined,'gauge-row');
  const summary = create('div');
  summary.append(create('h2',demo ? 'Demo AI score' : 'Fully AI-generated probability'),
    create('p',data.mode+' · Confidence: '+data.confidence,'small'));
  layout.append(gauge(data.score),summary);
  const metrics = create('div',undefined,'metrics');
  const s = data.signals;
  metrics.append(metric('Model perplexity',s.perplexity ?? 'Unavailable'),
    metric('Burstiness',s.burstiness+' words SD'),metric('Vocabulary diversity',s.diversity+'%'),
    metric('Repeated trigrams',s.repetition+'%'), metric('Unigram perplexity',s.unigram_perplexity));
  const notice = demo ? 'This demo uses sentence-length rules. Its scores are illustrative and cannot establish authorship.' :
    'Detectors return probabilistic estimates. False positives and false negatives are possible.';
  const key = create('div',undefined,'key');
  const names = demo ? ['Demo low','Demo medium','Demo high'] : ['Likely human','Uncertain','Likely AI'];
  for (const [i,kind] of ['human','uncertain','ai'].entries()) key.append(create('mark',names[i],kind));
  const spans = data.sentences.map(([a,b,number]) => [a,b,number < 35 ? 'human' : number < 65 ? 'uncertain' : 'ai']);
  const numeric = {human:1,uncertain:2,ai:3};
  results.append(layout,create('p',notice,'small'),metrics,key,passage(text,spans,demo),
    reportAction(text,[`AI score: ${data.score}% · ${data.mode}`,`Confidence: ${data.confidence}`,notice,time],spans.map(([a,b,k])=>[a,b,numeric[k]])));
  return data.score+'%'+(demo ? ' (demo)' : '');
}
function safeLink(value) {
  try { const url = new URL(value); return ['https:','http:'].includes(url.protocol) ? url.href : ''; }
  catch { return ''; }
}
function similarity(text,data,time) {
  const cover = Object.entries(data.queried).map(([name,entries]) => {
    const counts = Object.fromEntries([...new Set(entries)].map(status => [status,entries.filter(x=>x===status).length]));
    return name+' · '+Object.entries(counts).map(([key,val])=>`${val} ${key}`).join(', ');
  });
  const note = `Compared ${data.compared_sources} source texts or excerpts from ${data.sampled_phrases} sampled online phrases. Coverage is limited to material returned or supplied. Unmatched text can still appear elsewhere.`;
  const metrics = create('div',undefined,'metrics');
  metrics.append(metric('Similarity',data.analyzed && data.compared_sources ? data.score+'%' : 'Unavailable'),
    metric('Unmatched in this scan',data.analyzed && data.compared_sources ? data.unique+'%' : 'Unavailable'),metric('Analyzed words',data.analyzed));
  results.append(create('h2','Similarity report'),metrics,create('p',note,'small'),create('h3','Source coverage'));
  const coverage = create('ul');
  for (const line of cover) coverage.append(create('li',line));
  results.append(cover.length ? coverage : create('p','No sources queried.'));
  const spans = data.passages.map(([a,b])=>[a,b,'match']);
  results.append(passage(text,spans),create('h3','Matched sources'));
  for (const item of data.sources) {
    const row = create('div',undefined,'source');
    const address = safeLink(item.url);
    const title = create(address ? 'a' : 'strong',item.title);
    if (address) { title.href=address; title.target='_blank'; title.rel='noopener noreferrer'; }
    row.append(title,create('div',`${item.type} · ${item.percentage}% · ${item.words} matched words`,'small'));
    results.append(row);
  }
  if (!data.sources.length) results.append(create('p','No matched passages in the available source material.'));
  const score = data.analyzed && data.compared_sources ? data.score+'%' : 'Unavailable';
  results.append(reportAction(text,[`Similarity: ${score}`,`Unmatched: ${score==='Unavailable' ? score : data.unique+'%'}`,time,note,...cover,
    ...data.sources.map(s=>`${s.title} · ${s.type} · ${s.percentage}% · ${s.words} words · ${s.url}`)],spans.map(([a,b])=>[a,b,0])));
  return score+' similarity';
}
function showDiff(a,b,kind) {
  // Diff words with bounded look-ahead, retaining all original whitespace.
  const parts = a.match(/\s+|[^\s]+/gu) || [];
  const other = b.match(/\s+|[^\s]+/gu) || [];
  const pane = create('div',undefined,'passage');
  pane.dir = 'auto';
  pane.tabIndex = 0;
  let j = 0;
  let pending = '';
  let changed = false;
  function flush() {
    if (pending) pane.append(changed ? create('mark',pending,kind) : document.createTextNode(pending));
    pending = '';
  }
  for (const current of parts) {
    const found = other.slice(j,j+12).indexOf(current);
    const nextChanged = found < 0;
    if (changed !== nextChanged) flush();
    changed = nextChanged;
    pending += current;
    if (found >= 0) j += found+1;
  }
  flush();
  return pane;
}
function rewritten(text,data) {
  const diff = create('div',undefined,'diff');
  const before = create('div'), after = create('div');
  before.append(create('h3','Original'),showDiff(text,data.text,'removed'));
  after.append(create('h3','Rewrite'),showDiff(data.text,text,'added'));
  diff.append(before,after);
  const controls = create('div',undefined,'actions');
  controls.append(button('Copy',async () => { await navigator.clipboard.writeText(data.text); $('announce').textContent='Rewrite copied.'; }),
    button('Download TXT',()=>download(new Blob([data.text],{type:'text/plain'}),'textguard-rewrite.txt')),
    button('Download DOCX',async ()=>download(await (await call('/api/docx',{text:data.text})).blob(),'textguard-rewrite.docx')),
    button('Regenerate',()=>{variant+=1; return run('rewrite',text);}),
    button('Recheck AI and similarity',async ()=>{
      setText(data.text);
      select('similarity');
      await run('similarity',data.text,true);
    }));
  results.append(create('h2','Rewritten text'),create('p',data.mode,'small'),diff,controls);
  return words(data.text)+' words';
}
async function streamScan(payload,signal) {
  const response = await call('/api/similarity',payload,signal);
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let pending = '', result;
  try {
    for (;;) {
      const {value,done} = await reader.read();
      if (done) break;
      pending += value;
      const lines = pending.split('\n');
      pending = lines.pop();
      for (const line of lines) {
        if (!line) continue;
        const row = JSON.parse(line);
        busy(row.stage,row.progress);
        if (row.result) result = row.result;
      }
    }
  } finally { reader.releaseLock(); }
  if (!result) throw new Error('The scan did not complete. Try again.');
  return result;
}
async function run(tool=current,text=input.value,recheck=false) {
  if (!words(text) || words(text)>25000 || [...text].length>600000) {
    error('Enter 1 to 25,000 words and at most 600,000 characters.');
    return;
  }
  if (controller) controller.abort();
  const request = new AbortController();
  controller = request;
  const {signal} = request;
  $('run').disabled = true;
  results.hidden = true;
  error();
  busy(tool === 'similarity' ? 'Preparing source scan' : tool === 'detect' ? 'Analyzing text' : 'Rewriting text');
  const payload = {text,language:$('language').value,external:$('external').checked && (!$('external').disabled || recheck), mode:$('mode').value,
    strength:Number($('strength').value),variant};
  try {
    const timestamp = new Date().toISOString();
    let data, ai, summary;
    if (tool === 'similarity') {
      if (recheck) ai = await (await call('/api/detect',payload,signal)).json();
      data = await streamScan({...payload,references,exclusions:[...$('similarOptions').querySelectorAll('input[type=checkbox]:checked')].map(x=>x.value)},signal);
    } else data = await (await call('/api/'+tool,payload,signal)).json();
    if (signal.aborted) return;
    results.replaceChildren();
    if (ai) detector(text,ai,timestamp);
    if (tool === 'similarity') summary = similarity(text,data,data.timestamp);
    if (tool === 'detect') summary = detector(text,data,timestamp);
    if (tool === 'rewrite') summary = rewritten(text,data);
    results.hidden = false;
    results.tabIndex = -1;
    results.focus();
    saveHistory(tool,summary,timestamp);
  } catch (e) {
    if (e.name !== 'AbortError') error(e.message);
  } finally {
    if (controller === request) {
      controller = undefined;
      $('run').disabled = false;
      $('progressBox').hidden = true;
    }
  }
}
async function extract(file) {
  if (file.size>10*1024*1024) throw new Error('Files must be 10 MB or smaller.');
  const data = new FormData();
  data.append('file',file);
  return (await (await call('/api/extract',data)).json()).text;
}
async function loadFile(file) {
  if (!file) return;
  error();
  const stamp = ++generation;
  try {
    const text = await extract(file);
    if (generation === stamp) setText(text);
  } catch(e) { if (generation === stamp) error(e.message); }
  finally { $('file').value = ''; }
}
function showReferences() {
  $('refList').replaceChildren();
  for (const ref of references) {
    const li = create('li',ref.title);
    li.append(button('Remove',() => {
      references = references.filter(item=>item!==ref);
      showReferences();
    }));
    $('refList').append(li);
  }
}
$('references').addEventListener('change',async event=>{
  const files = [...event.target.files];
  event.target.value = '';
  if (activeUploads || files.length+references.length>5) { error('Wait for pending uploads and keep at most five private sources.'); return; }
  const stamp = generation;
  activeUploads++;
  error();
  try {
    for (const file of files) {
      const text = await extract(file);
      if (generation !== stamp) break;
      references.push({title:file.name.slice(0,150),text});
      showReferences();
    }
  } catch(e) { if (generation === stamp) error(e.message); }
  finally { activeUploads--; }
});
$('file').addEventListener('change',event=>loadFile(event.target.files[0]));
const drop=$('drop');
drop.addEventListener('dragover',event=>{event.preventDefault();drop.classList.add('drag');});
drop.addEventListener('dragleave',()=>drop.classList.remove('drag'));
drop.addEventListener('drop',event=>{event.preventDefault();drop.classList.remove('drag');loadFile(event.dataTransfer.files[0]);});
$('tabs').addEventListener('click',event=>{if(event.target.dataset.tool) select(event.target.dataset.tool);});
$('input').addEventListener('input',updateCount);
$('clear').addEventListener('click',()=>{generation++;setText('');error();input.focus();});
$('run').addEventListener('click',()=>run());
$('sample').addEventListener('click',()=>{if(config.sample) setText(config.sample+' In order to evaluate this material, the study compares the measured responses of two electrodes.');});
$('strength').addEventListener('input',()=>{$('strengthText').value=['Low','Medium','High'][Number($('strength').value)-1];});
function theme(dark) {
  document.documentElement.dataset.theme=dark?'dark':'light';
  $('theme').setAttribute('aria-pressed',String(dark));
  $('theme').textContent=dark?'Light mode':'Dark mode';
}
theme(local.read('tg-dark',matchMedia('(prefers-color-scheme: dark)').matches));
$('theme').addEventListener('click',()=>{
  const dark=document.documentElement.dataset.theme!=='dark';
  theme(dark);
  local.write('tg-dark',dark);
});
$('remember').checked=local.read('tg-consent',false);
$('remember').addEventListener('change',()=>local.write('tg-consent',$('remember').checked));
$('delete').addEventListener('click',()=>{
  controller?.abort();
  generation++;
  references=[];
  history=[];
  for (const key of ['tg-history','tg-consent']) local.remove(key);
  $('remember').checked=false;
  $('external').checked=false;
  results.replaceChildren();
  results.hidden=true;
  setText('');
  showReferences();
  showHistory();
  error();
  $('announce').textContent='Workspace and saved history deleted.';
});
fetch('/api/config').then(response=>{
  if (!response.ok) throw new Error();
  return response.json();
}).then(data=>{config=data;select(current);}).catch(()=>error('The server is unavailable. Restart TextGuard and refresh.'));
showHistory();
updateCount();
select(current);
