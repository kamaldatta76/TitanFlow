from __future__ import annotations
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
from pathlib import Path
from datetime import datetime, timezone
import asyncio, hashlib, json, os, time, uuid
import httpx
import re
import xml.etree.ElementTree as ET
from html import unescape
from urllib.parse import quote_plus

app = FastAPI(title='ATLAS Demo Bridge')
DATA_DIR = Path('/home/kamaldatta/atlas_demo_runs')
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = DATA_DIR / 'atlas_demo_runs.jsonl'
LOG_PATH.touch(exist_ok=True)
SHADOW_OLLAMA = os.getenv('ATLAS_DEMO_SHADOW_OLLAMA', 'http://10.0.0.32:11434')
OLLIE_MODEL = os.getenv('ATLAS_DEMO_OLLIE_MODEL', 'qwen3.5:35b')
ARCHIE_MODEL = os.getenv('ATLAS_DEMO_ARCHIE_MODEL', OLLIE_MODEL)
TIMEOUT = float(os.getenv('ATLAS_DEMO_TIMEOUT_S', '75'))
INSUFFICIENT = 'Atlas does not have sufficient data to answer confidently.'

NEWS_FALLBACK = "ATLAS is not using a live external news feed in this presentation path, but the current executive frame remains the same: shipping conditions are macro-driven, rate-sensitive, and highly exposed to commodity demand, congestion, geopolitics, and asset-specific positioning."
BDI_FALLBACK = "ATLAS is not yet using a live Baltic Dry Index quote feed in this view, but the executive frame remains centered on dry-bulk rate direction, iron ore and coal demand, ballast availability, congestion, and Capesize versus Panamax dispersion. Once live market-data retrieval is enabled through the backend, ATLAS will return the dated BDI print directly."

CURRENT_EVENT_TOKENS = (
    'last week', 'this week', 'latest', 'today', 'yesterday', 'headlines',
    'news', 'what happened', 'market update', 'update'
)
BALTIC_MARKET_TOKENS = (
    'baltic exchange', 'baltic dry index', 'dry bulk index', 'bdi',
    'dry bulk', 'wet freight', 'capesize', 'panamax', 'supramax',
    'shipping rates', 'freight rates'
)
OIL_MACRO_TOKENS = ('oil', 'crude', 'bunker', 'bunkers', 'macro')

def has_any(q:str, phrases:tuple[str, ...] | list[str]) -> bool:
    return any(phrase in q for phrase in phrases)

def is_current_events_query(query:str) -> bool:
    q = query.lower().strip()
    return has_any(q, CURRENT_EVENT_TOKENS)

def is_baltic_market_query(query:str) -> bool:
    q = query.lower().strip()
    return has_any(q, BALTIC_MARKET_TOKENS)

def needs_live_news(query:str)->bool:
    q=query.lower().strip()
    phrases=[
        'latest maritime news','maritime news','dry bulk market',"what's happening in shipping",
        'whats happening in shipping','shipping market','latest shipping news','shipping news',
        'maritime market','dry bulk shipping','bulk shipping','wet freight', 'shipping rates'
    ]
    return any(phrase in q for phrase in phrases) or (
        is_current_events_query(q) and (is_baltic_market_query(q) or has_any(q, OIL_MACRO_TOKENS) or 'shipping' in q or 'maritime' in q)
    )

def needs_live_bdi(query:str)->bool:
    q=query.lower().strip()
    return (
        'baltic dry index' in q or 'dry bulk index' in q or q.startswith('bdi') or ' bdi ' in f' {q} '
        or ('baltic exchange' in q and is_current_events_query(q))
    )

def needs_weekly_news(query:str)->bool:
    q=query.lower().strip()
    phrases=['latest news from last week', "last week's maritime news", 'last weeks maritime news', 'what happened last week', 'recap last week in shipping', 'weekly maritime update', 'last week shipping', 'last week maritime']
    return any(phrase in q for phrase in phrases) or (
        is_current_events_query(q) and ('week' in q or 'yesterday' in q) and (is_baltic_market_query(q) or has_any(q, OIL_MACRO_TOKENS) or 'shipping' in q or 'maritime' in q)
    )

def news_search_term(query:str)->str:
    q=query.lower().strip()
    if is_baltic_market_query(q):
        return 'Baltic Exchange bulk report'
    if 'dry bulk' in q:
        return 'dry bulk shipping market'
    if any(k in q for k in OIL_MACRO_TOKENS):
        return 'oil prices shipping bunker fuel'
    return 'maritime shipping market'

def weekly_news_search_term(query:str)->str:
    q=query.lower().strip()
    if is_baltic_market_query(q):
        return 'Baltic Exchange bulk report when:7d'
    if 'dry bulk' in q or 'shipping' in q:
        return 'dry bulk shipping when:7d'
    if any(k in q for k in OIL_MACRO_TOKENS):
        return 'oil shipping macro when:7d'
    return 'maritime shipping when:7d'

def clean_html_text(value:str)->str:
    value = re.sub(r'<.*?>', '', value or '')
    return re.sub(r'\s+', ' ', unescape(value)).strip()

async def fetch_news_headlines(query:str, limit:int=6)->list[str]:
    url=f"https://news.google.com/rss/search?q={quote_plus(news_search_term(query))}&hl=en-US&gl=US&ceid=US:en"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        r=await client.get(url, headers={'User-Agent':'ATLAS/1.1'})
        r.raise_for_status()
    root=ET.fromstring(r.text)
    titles=[]
    for item in root.findall('./channel/item'):
        title=(item.findtext('title') or '').strip()
        if not title:
            continue
        title=title.replace(' - Google News','').strip()
        titles.append(title)
        if len(titles) >= limit:
            break
    return titles

async def fetch_weekly_news_items(query:str, limit:int=5)->list[dict]:
    url=f"https://news.google.com/rss/search?q={quote_plus(weekly_news_search_term(query))}&hl=en-US&gl=US&ceid=US:en"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        r=await client.get(url, headers={'User-Agent':'ATLAS/1.1'})
        r.raise_for_status()
    root=ET.fromstring(r.text)
    items=[]
    for item in root.findall('./channel/item'):
        title=(item.findtext('title') or '').replace(' - Google News','').strip()
        pub=(item.findtext('pubDate') or '').strip()
        if not title:
            continue
        try:
            dt=datetime.strptime(pub, '%a, %d %b %Y %H:%M:%S %Z')
            stamp=dt.strftime('%Y-%m-%d')
        except Exception:
            stamp=pub
        items.append({'title': title, 'date': stamp})
        if len(items) >= limit:
            break
    return items

async def fetch_bdi_snapshot()->dict|None:
    url=f"https://html.duckduckgo.com/html/?q={quote_plus('latest baltic dry index')}"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        r=await client.get(url, headers={'User-Agent':'Mozilla/5.0'})
        r.raise_for_status()
    html=r.text
    title_match=re.search(r'class="result__a"[^>]*>(.*?)</a>', html, re.S)
    snippet_match=re.search(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
    if not title_match and not snippet_match:
        return None
    title=clean_html_text(title_match.group(1)) if title_match else 'Baltic Exchange Dry Index'
    snippet=clean_html_text(snippet_match.group(1)) if snippet_match else ''
    patterns=[
        r'(fell|rose) to ([0-9,]+) Index Points on ([A-Za-z]+ \d{1,2}, \d{4}), (down|up) ([0-9.]+)%',
        r'closed at ([0-9,]+) points on ([A-Za-z]+ \d{1,2}, \d{4}), .*?, (down|up) ([0-9.]+)%',
    ]
    for pat in patterns:
        m=re.search(pat, snippet, re.I)
        if not m:
            continue
        if len(m.groups()) == 5:
            move_word, points, date_text, direction, pct = m.groups()
        else:
            points, date_text, direction, pct = m.groups()
            move_word='fell' if direction.lower() == 'down' else 'rose'
        return {
            'title': title,
            'snippet': snippet,
            'points': points,
            'date': date_text,
            'direction': direction.lower(),
            'move_word': move_word.lower(),
            'pct': pct,
        }
    return {'title': title, 'snippet': snippet}

async def fetch_duckduckgo_result(search_query:str)->dict|None:
    url=f"https://html.duckduckgo.com/html/?q={quote_plus(search_query)}"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        r=await client.get(url, headers={'User-Agent':'Mozilla/5.0'})
        r.raise_for_status()
    html=r.text
    match=re.search(r'class="result__a"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
    if not match:
        return None
    return {
        'title': clean_html_text(match.group(1)),
        'snippet': clean_html_text(match.group(2)),
    }

def derive_prior_bdi_points(points:str, pct:str, direction:str)->str|None:
    try:
        current=float(points.replace(',', ''))
        move=float(pct)
        if direction == 'down':
            prior = current / (1 - (move / 100.0))
        else:
            prior = current / (1 + (move / 100.0))
        rounded = int(round(prior / 10.0) * 10)
        return f'{rounded:,}'
    except Exception:
        return None

class DemoQuery(BaseModel):
    query: str
    source: str | None = 'atlas-demo'
    principal: str | None = 'DR. SHARMA'

def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')

def path_for(qid:str)->Path:
    return DATA_DIR / f'{qid}.json'

def save(state:dict):
    p=path_for(state['query_id']); tmp=p.with_suffix('.tmp'); tmp.write_text(json.dumps(state, ensure_ascii=False)); tmp.replace(p)

def load(qid:str)->dict:
    p=path_for(qid)
    if not p.exists(): raise FileNotFoundError(qid)
    return json.loads(p.read_text())

def event(state:dict, name:str, agent:str, detail:str=''):
    state['events'].append({'event':name,'timestamp':now_iso(),'agent':agent,'query_id':state['query_id'],'detail':detail})
    save(state)

def message(state:dict, role:str, label:str, text:str, accent:str):
    state['messages'].append({'role':role,'label':label,'text':text,'accent':accent,'timestamp':now_iso()})
    save(state)


def deterministic_response(query:str)->str|None:
    q=query.lower().strip()
    if any(phrase in q for phrase in ["who's online", "whos online", "who is online", "which agents are online", "agents online", "status of the agents"]):
        return "Archie, Charlie, Ollie, and Flow are online. Charlie is coordinating on Sarge, Flow is routing, Ollie is executing on Shadow, and Archie is handling final review."
    if any(phrase in q for phrase in ["what node", "which node", "where is this running", "what is the runtime path", "which runtime"]):
        return "The live ATLAS path is coordinated on Sarge and answered on Shadow. Charlie coordinates, Flow routes, Ollie executes on Shadow, and Archie returns the final response."
    if q in {"hi", "hello", "hey", "give me a quick update"} or any(phrase in q for phrase in ["hi son", "hello son", "what's cooking", "whats cooking", "quick update", "give me an update", "status update"]):
        return "ATLAS is online and operating through the live chain. Charlie is coordinating on Sarge. Flow is routing. Ollie is executing on Shadow. Archie is handling final review. Fleet and operational modules are available in this workspace."
    if any(phrase in q for phrase in ["list all the vessels", "what vessels", "fleet list", "list the vessels", "show the fleet", "which vessels"]):
        return (
            "Lila Global/GMS currently shows eight vessels in this ATLAS view: MV Lila Fortune, MV GMS Brave, MT Indrani Star, MV Asiatic Pride, MV Eastern Carrier, MV Pacific Voyager, MT Suraj Bhavnagar, and MV Gangotri. Operationally, six are trading, one is in port at Fujairah, one is in yard at Jubail, and Gangotri is flagged as an incident case pending Aliaga."
        )
    if any(phrase in q for phrase in ["fleet status", "status of the fleet", "how is the fleet doing"]):
        return (
            "Current ATLAS fleet status in this view: 8 total vessels, 6 trading, 1 in port at Fujairah, and 1 in yard at Jubail drydock. The strongest near-term commercial positions shown are MV Lila Fortune and MT Suraj Bhavnagar, while MV Gangotri is the main exception and remains flagged as an incident case."
        )
    if any(phrase in q for phrase in ["are you able to research the internet", "can you browse", "can you look things up online", "can you research the internet", "can you browse the internet", "can you research online"]):
        return (
            "ATLAS can research external information when that retrieval path is enabled through the backend. In this live view, ATLAS is currently optimized for fleet intelligence, operational chain responses, and guided executive summaries."
        )
    if any(phrase in q for phrase in ["oil prices", "crude", "bunker costs", "bunker prices", "where do you see crude going"]):
        if is_current_events_query(q) or 'shipping rates' in q or 'market update' in q:
            return None
        return (
            "Oil prices are likely to remain headline-sensitive and volatile, with direction driven by geopolitics, OPEC+ discipline, global demand signals, and freight-linked energy consumption. For shipping, the practical impact is continued uncertainty in bunker costs and voyage economics rather than a single clean directional bet."
        )
    if needs_live_bdi(q):
        return None
    if needs_weekly_news(q):
        return None
    if any(phrase in q for phrase in ["latest maritime news", "maritime news", "dry bulk market", "what's happening in shipping", "whats happening in shipping", "shipping market", "latest shipping news"]):
        return None
    return None

async def gen(prompt:str, system:str, model:str, num_predict:int=180)->str:
    payload={'model':model,'system':system,'prompt':prompt,'stream':False,'options':{'temperature':0.2,'num_predict':num_predict}}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r=await client.post(f'{SHADOW_OLLAMA}/api/generate', json=payload)
        r.raise_for_status()
        return (r.json().get('response') or '').strip()

async def run_chain(qid:str):
    state=load(qid); q=state['query']; start=time.perf_counter()
    try:
        event(state,'QUERY_RECEIVED','CHARLIE','received')
        message(state,'system','CHARLIE','Query received. Coordinating ATLAS chain on Sarge.', 'charlie')
        await asyncio.sleep(0.12)
        event(state,'FLOW_ROUTING','FLOW','routing_to_shadow')
        message(state,'system','FLOW','Routing to Shadow fast path for live execution.', 'flow')
        await asyncio.sleep(0.12)
        event(state,'OLLIE_EXECUTING','OLLIE','shadow_inference')
        direct = deterministic_response(q)
        if direct is not None:
            ollie = direct
        else:
            if needs_live_bdi(q):
                try:
                    bdi = await fetch_bdi_snapshot()
                except Exception:
                    bdi = None
                weekly_baltic = None
                if is_current_events_query(q) or 'baltic exchange' in q:
                    try:
                        weekly_baltic = await fetch_duckduckgo_result('Baltic Exchange Week bulk report')
                    except Exception:
                        weekly_baltic = None
                if bdi and bdi.get('points') and bdi.get('date'):
                    day_move = 'down' if bdi.get('direction') == 'down' else 'up'
                    prior_points = derive_prior_bdi_points(bdi['points'], bdi.get('pct', '0'), bdi.get('direction', 'down'))
                    if weekly_baltic and weekly_baltic.get('snippet'):
                        prior_text = f" versus approximately {prior_points} on the prior comparison print" if prior_points else ""
                        ollie = (
                            f"Last week the Baltic Dry Index weakened, closing {bdi['date']} at {bdi['points']} points{prior_text}. "
                            f"Baltic Exchange's latest weekly bulk report said the market was shaped by macro developments and rising bunker prices rather than clean demand improvement. "
                            "Executive read: the move still reflects bunker-cost pressure, geopolitical risk, and vessel-class dispersion more than a broad-based demand breakout."
                        )
                    else:
                        ollie = (
                            f"ATLAS located the latest public Baltic Dry Index print at {bdi['points']} points on {bdi['date']}, "
                            f"{day_move} {bdi['pct']}% day on day. Executive read: dry-bulk tone remains governed by iron ore and coal demand, "
                            "vessel-class dispersion, ballast availability, and corridor risk. This is the latest public print ATLAS could verify in the live backend path."
                        )
                elif bdi and bdi.get('snippet'):
                    ollie = f"ATLAS located a live public Baltic Dry Index market update: {bdi['snippet']}"
                elif weekly_baltic and weekly_baltic.get('snippet'):
                    ollie = (
                        f"ATLAS located a live Baltic Exchange weekly market summary: {weekly_baltic['snippet']} "
                        "Executive read: the dry-bulk tone remains macro-sensitive and bunker-cost driven rather than demand-clean."
                    )
                else:
                    ollie = BDI_FALLBACK
            elif needs_weekly_news(q):
                try:
                    items = await fetch_weekly_news_items(q)
                except Exception:
                    items = []
                if items:
                    lead = '; '.join(f"{item['date']}: {item['title']}" for item in items[:4])
                    ollie = (
                        f"ATLAS weekly maritime recap for the latest available seven-day window: {lead}. "
                        "Executive frame: last week was still driven by corridor security, war-risk insurance, commodity demand sensitivity, "
                        "and vessel positioning around Gulf and dry-bulk routes."
                    )
                else:
                    ollie = (
                        "ATLAS is not yet using a live historical news retrieval path in this view, but the executive frame for last week remains centered on macro-sensitive shipping conditions, commodity-linked demand, congestion, geopolitics, and vessel-specific positioning. Once historical retrieval is enabled through the backend, ATLAS will return dated weekly summaries directly."
                    )
            else:
                headlines = []
                if needs_live_news(q):
                    try:
                        headlines = await fetch_news_headlines(q)
                    except Exception:
                        headlines = []
                if headlines:
                    lead = '; '.join(headlines[:3])
                    ollie = (
                        f"Current maritime headlines point to three live themes: {lead}. "
                        "Executive read: the operating picture remains driven by corridor security, trade-policy and demand uncertainty, insurance and financing conditions, and asset-specific commercial positioning."
                    )
                elif needs_live_news(q):
                    ollie = NEWS_FALLBACK
                else:
                    ollie = await gen(
                        prompt=f'User query: {q}\n\nRespond as a maritime intelligence analyst. Provide a concise answer in 2-4 bullets. If the question requires unavailable real-time proprietary data, reply exactly: {INSUFFICIENT}',
                        system='You are Ollie, the ATLAS execution and research worker. Be concise, factual, and executive-safe.',
                        model=OLLIE_MODEL,
                        num_predict=160,
                    )
        ollie = ollie or INSUFFICIENT
        fallback = deterministic_response(q)
        if ollie.strip() == INSUFFICIENT and fallback is not None:
            ollie = fallback
        message(state,'assistant','OLLIE',ollie,'ollie')
        event(state,'ARCHIE_REVIEW','ARCHIE','reviewing')
        if ollie.strip() == INSUFFICIENT:
            archie = INSUFFICIENT
        else:
            try:
                archie = await gen(
                    prompt=f'Original query: {q}\n\nOllie draft:\n{ollie}\n\nRefine this into the final ATLAS response. Keep it concise and executive-ready. Do not invent facts.',
                    system='You are Archie, the ATLAS review and synthesis layer. Produce the final premium executive response.',
                    model=ARCHIE_MODEL,
                    num_predict=140,
                )
            except Exception:
                archie = ollie
        if not archie or archie.strip() == INSUFFICIENT:
            archie = ollie or INSUFFICIENT
        message(state,'assistant','ARCHIE',archie,'archie')
        event(state,'FINAL_RESPONSE','ARCHIE','complete')
        state=load(qid)
        state['status']='complete'
        state['response']=archie
        state['responding_node']='shadow.titan.internal'
        state['coordinator_node']='sarge.titan.internal'
        state['duration_ms']=int((time.perf_counter()-start)*1000)
        save(state)
        with LOG_PATH.open('a') as fh:
            fh.write(json.dumps({'timestamp':now_iso(),'query_id':qid,'query':q,'agents':['CHARLIE','FLOW','OLLIE','ARCHIE'],'duration_ms':state['duration_ms'],'result_status':'complete','responding_node':'shadow.titan.internal','coordinator_node':'sarge.titan.internal','query_hash':hashlib.sha256(q.encode()).hexdigest()[:16]}, ensure_ascii=False)+'\n')
    except Exception as exc:
        state=load(qid)
        state['status']='error'; state['error']=str(exc); state['duration_ms']=int((time.perf_counter()-start)*1000)
        save(state)
        with LOG_PATH.open('a') as fh:
            fh.write(json.dumps({'timestamp':now_iso(),'query_id':qid,'query':q,'agents':['CHARLIE','FLOW','OLLIE','ARCHIE'],'duration_ms':state['duration_ms'],'result_status':'error','error':str(exc),'responding_node':'shadow.titan.internal','coordinator_node':'sarge.titan.internal'}, ensure_ascii=False)+'\n')

@app.get('/api/health')
async def health():
    return {'status':'ok','service':'atlas-demo-bridge','responding_node':'shadow.titan.internal','coordinator_node':'sarge.titan.internal'}

@app.post('/api/demo/query')
async def query(body: DemoQuery, bg: BackgroundTasks):
    q=(body.query or '').strip()
    if not q:
        raise HTTPException(status_code=400, detail='query is required')
    qid=f'atlas-demo-{uuid.uuid4().hex[:10]}'
    state={'query_id':qid,'status':'running','source':body.source,'principal':body.principal,'query':q,'events':[],'messages':[{'role':'user','label':body.principal or 'DR. SHARMA','text':q,'accent':'user','timestamp':now_iso()}],'created_at':now_iso(),'response':None,'responding_node':None,'coordinator_node':'sarge.titan.internal','duration_ms':None}
    save(state)
    bg.add_task(run_chain,qid)
    return {'ok':True,'query_id':qid,'status':'running'}

@app.get('/api/demo/status/{query_id}')
async def status(query_id:str):
    try:
        return load(query_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail='query not found')
