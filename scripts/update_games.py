#!/usr/bin/env python3
"""Daily Altistats collector. No undocumented API endpoint is assumed.

Uses a real browser and inspects JSON responses / rendered match elements.
Fails loudly if the upstream site doesn't expose complete records. Existing
archived games are never deleted or replaced by partial scraped data.
"""
import argparse
import calendar
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

BASE = Path(__file__).resolve().parents[1]
DATA_PATH = BASE / 'data' / 'matches.json'
JS_PATH = BASE / 'data' / 'matches.js'
META_PATH = BASE / 'data' / 'status.json'
DEBUG = BASE / 'debug'
SITE = 'https://altistats.com/games'
UUID_RE = re.compile(r'(?i)\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b')
REPLAY_BASE = 'https://www.altistats.com/viewer/?f='


def date_of(value):
    if isinstance(value, dict):
        return date_of(value.get('date') or value.get('started_at') or value.get('timestamp'))
    if isinstance(value, (int, float)):
        try: return datetime.fromtimestamp(value / (1000 if value > 1e11 else 1), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        except (ValueError, OverflowError, OSError): return None
    if not value: return None
    s = str(value).strip()
    try:
        if re.fullmatch(r'\d{4}-\d\d-\d\d', s):
            return s + 'T00:00:00Z'
        if s.endswith('Z'): s = s[:-1] + '+00:00'
        d = datetime.fromisoformat(s)
        if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    except (ValueError, TypeError): return None


def duration_of(value):
    if isinstance(value, (int,float)):
        return round(value) if 0 <= value <= 7200 else None
    if not value: return None
    s = str(value).strip().lower()
    if re.fullmatch(r'\d+(?:\.\d+)?',s):return duration_of(float(s))
    m = re.fullmatch(r'(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?',s)
    if m and any(t is not None for t in m.groups()):
        return duration_of(int(m[1] or 0)*3600+int(m[2] or 0)*60+int(m[3] or 0))
    m = re.fullmatch(r'(?:(\d+):)?(\d+):(\d+)',s)
    if m:return duration_of(int(m[1] or 0)*3600+int(m[2])*60+int(m[3]))
    return None


def name_of(x):
    if isinstance(x, str): return x.strip()
    if isinstance(x, dict):
        for key in ('handle','username','display_name','displayName','nick','nickname','name','player_name'):
            if isinstance(x.get(key),str):return x[key].strip()
    return ''


def roster(value):
    if isinstance(value,str):
        return [x.strip() for x in re.split(r'[,;|]',value) if x.strip()]
    if isinstance(value,dict):
        return roster(value.get('players') or value.get('members') or value.get('roster') or [])
    if isinstance(value,list):return [v for item in value if (v:=name_of(item))]
    return []


def pick(node,*keys):
    for key in keys:
        v = node.get(key)
        if v is not None and v != '': return v
    return None


def scalar_score(v):
    if isinstance(v,dict):v=pick(v,'score','goals','points')
    try:
        n=int(v)
        return n if 0 <= n <= 100 else None
    except (ValueError,TypeError):return None


def normalize(node, series_hint=0, date_hint=None):
    """Require all match fields; NEVER manufacture missing roster/scores."""
    if not isinstance(node,dict):return None
    rawid=pick(node,'game_id','gameId','uuid','replay_id','replayId','replay','file','filename','id','url','href','download_url')
    m=UUID_RE.search(str(rawid)) if rawid is not None else None
    if not m:
        for v in node.values():
            if isinstance(v,str) and ('.pb' in v or '/viewer/' in v):
                m=UUID_RE.search(v)
                if m:break
    if not m:return None
    id=m.group(0).lower()
    dt=date_of(pick(node,'date','started_at','startedAt','start_time','startTime','start','timestamp','created_at','createdAt','time') or date_hint)
    dur=duration_of(pick(node,'duration_seconds','durationSeconds','duration','length_seconds','lengthSeconds','length','elapsed_seconds','elapsed','game_length','gameLength'))
    map_value=pick(node,'map','map_name','mapName','level','levelName','map_id')
    if isinstance(map_value,dict): map_value=pick(map_value,'name','id','map_name')
    a=roster(pick(node,'team1','blue','blue_team','blueTeam','players1','blue_players','bluePlayers'))
    b=roster(pick(node,'team2','red','red_team','redTeam','players2','red_players','redPlayers'))
    sc1=scalar_score(pick(node,'score1','blue_score','blueScore','blue_goals','blueGoals'))
    sc2=scalar_score(pick(node,'score2','red_score','redScore','red_goals','redGoals'))
    if isinstance(node.get('teams'),list) and len(node['teams'])>=2:
        teams=node['teams']
        a=a or roster(teams[0]);b=b or roster(teams[1])
        if sc1 is None:sc1=scalar_score(teams[0])
        if sc2 is None:sc2=scalar_score(teams[1])
    if isinstance(node.get('score'),(list,tuple)) and len(node['score'])>=2:
        if sc1 is None:sc1=scalar_score(node['score'][0])
        if sc2 is None:sc2=scalar_score(node['score'][1])
    if isinstance(node.get('scores'),(list,tuple)) and len(node['scores'])>=2:
        if sc1 is None:sc1=scalar_score(node['scores'][0])
        if sc2 is None:sc2=scalar_score(node['scores'][1])
    if isinstance(node.get('scores'),dict):
        if sc1 is None:sc1=scalar_score(pick(node['scores'],'blue','team1','home'))
        if sc2 is None:sc2=scalar_score(pick(node['scores'],'red','team2','away'))
    if isinstance(node.get('players'),list):
        split={0:[],1:[]}
        for player in node['players']:
            if not isinstance(player,dict):continue
            side=pick(player,'team','team_id','teamId','side','team_index')
            side=str(side).lower()
            if side in ('0','1','blue','red'):
                split[0 if side in ('0','blue') else 1].append(name_of(player))
        a=a or [x for x in split[0] if x]
        b=b or [x for x in split[1] if x]
    if not (dt and dur is not None and map_value and len(a)>=2 and len(b)>=2 and sc1 is not None and sc2 is not None):return None
    if isinstance(node.get('series'),dict):sv=pick(node['series'],'id','series_id')
    else:sv=pick(node,'series','series_id','seriesId','tournament_id')
    try:ser=int(sv) if sv is not None else series_hint
    except (TypeError,ValueError):ser=series_hint
    if ser not in (0,1,2):ser=series_hint
    return [id,dt,dur,str(map_value),a,b,sc1,sc2,ser]


def scan(value, series_hint, out, max_nodes=120000):
    seen=[0]
    def walk(item,depth,context_date=None):
        if seen[0]>=max_nodes or depth>20:return
        seen[0]+=1
        if isinstance(item,dict):
            own_date=date_of(pick(item,'date','started_at','startedAt','start_time','startTime','timestamp','created_at'))
            hint=own_date or context_date
            rec=normalize(item,series_hint,hint)
            if rec:out[rec[0]]=rec
            for v in item.values():
                if isinstance(v,(dict,list)):walk(v,depth+1,hint)
        elif isinstance(item,list):
            for v in item:walk(v,depth+1,context_date)
    walk(value,0)
    return seen[0]


def load_existing():
    rows=json.loads(DATA_PATH.read_text())
    out={r[0]:r for r in rows}
    if len(out)!=len(rows):raise ValueError('Duplicate IDs in original archive')
    return out


def save(all_games, new_count, scanned, sources):
    values=sorted(all_games.values(),key=lambda r:r[1],reverse=True)
    # Make the JSON and JS sidecar atomically; a failed scrape must leave old archive intact.
    j=json.dumps(values,ensure_ascii=False,separators=(',',':'))
    js='window.ALTISTATS_DATA='+j+';\nwindow.ALTISTATS_LAST_REFRESHED='+json.dumps(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))+';\n'
    meta={"refreshed_utc":datetime.now(timezone.utc).isoformat(),"total_games":len(values),"new_games":new_count,"valid_records_seen":scanned,"sources":sources,"latest_game_utc":values[0][1]}
    for path,content in [(DATA_PATH,j+'\n'),(JS_PATH,js),(META_PATH,json.dumps(meta,indent=2)+'\n')]:
        tmp=path.with_suffix(path.suffix+'.tmp')
        tmp.write_text(content,encoding='utf-8')
        tmp.replace(path)


def candidate_day_buttons(page):
    # Discover interactive calendar day cells, without assuming a CSS class in upstream site.
    return page.evaluate('''() => {
      const all=[...document.querySelectorAll('main button,main a,main [role=button], main [data-date], main td, main .day')];
      const a=[];
      for(const e of all){
        let t=(e.innerText||e.textContent||'').trim(), d=e.getAttribute('data-date')||'';
        let n=t.match(/^([1-9]|[12][0-9]|3[01])(?:\\s|$)/);
        let rect=e.getBoundingClientRect();
        if(rect.width<8||rect.height<8||!((n&&t.length<65)||d)) continue;
        if(!n && !d) continue;
        a.push({text:t.slice(0,64),day:n?+n[1]:(+d.split('-').at(-1)||0),tag:e.tagName,cls:String(e.className).slice(0,100)});
      }
      return a;
    }''')


def crawl(days=45,headless=True):
    try:from playwright.sync_api import sync_playwright
    except ImportError as exc: raise RuntimeError('Install playwright (pip install playwright; playwright install chromium)') from exc
    result={};sources=[];DEBUG.mkdir(parents=True,exist_ok=True)
    now=datetime.now(timezone.utc)
    months=[]
    for off in range(days+1):
        m=(now-timedelta(days=off)).strftime('%Y-%m')
        if m not in months:months.append(m)
    if len(months)>3:months=months[:3]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=headless,args=['--no-sandbox'])
        ctx=browser.new_context(user_agent='Mozilla/5.0 (compatible; CommunityReplayIndex/1.0; +https://altistats.com/games)',viewport={'width':1440,'height':1100})
        page=ctx.new_page()
        global_series=[0,1,2]
        cur_series=[0,1,2]
        for series in cur_series:
            for month in months if series==0 else months[:1]:
                url=SITE+'?'+urlencode({'period':month,'series':series})
                received={'n':0,'json':0,'page':url,'candidateDays':[]}
                def response_handler(res,series=series):
                    try:
                        ct=res.headers.get('content-type','').lower()
                        if 'json' not in ct and not any(t in res.url for t in ['/api/', '.json', '/data/']):return
                        payload=res.json()
                        received['json']+=1
                        if received['json']<=3:
                            try:
                                preview=json.dumps(payload,ensure_ascii=False)
                                (DEBUG/f'payload_sample_{series}_{month}_{received["json"]}.json').write_text(preview[:150000],encoding='utf-8')
                            except Exception:pass
                        scan(payload,series,result)
                    except Exception:return
                page.on('response',response_handler)
                try:
                    resp=page.goto(url,wait_until='domcontentloaded',timeout=45000)
                    page.wait_for_timeout(2700)
                    if not resp or resp.status>=400:raise RuntimeError('HTTP '+str(resp.status if resp else 'no response'))
                    # Try common framework globals and hydrated / rendered state.
                    embedded=page.evaluate('''() => {
                      const keys=['__NEXT_DATA__','__NUXT__','__INITIAL_STATE__','__PRELOADED_STATE__','__data','games','matches'];
                      const out={};for(const key of keys){try {const o=window[key]; if(o&&JSON.stringify(o).length<20000000)out[key]=o}catch{}}
                      return out;
                    }''')
                    scan(embedded,series,result)
                    # JSON in script tags, for non-SSR frontend apps.
                    for txt in page.locator('script[type="application/json"],script[type="application/ld+json"]').all_text_contents():
                        try:scan(json.loads(txt),series,result)
                        except (ValueError,TypeError):pass
                    received['candidateDays']=candidate_day_buttons(page)
                    # A few days are generally selected by default; interact with visible calendar days
                    # to trigger the site's normal data requests without guessing hidden API routes.
                    selected_days=set()
                    daylist=sorted({x['day'] for x in received['candidateDays'] if x['day']},reverse=True)
                    for day in daylist:
                        try:
                            date=datetime.strptime(f'{month}-{day:02d}','%Y-%m-%d').replace(tzinfo=timezone.utc)
                            if date > now+timedelta(days=1) or date < now-timedelta(days=days):continue
                        except ValueError:continue
                        # Re-query each iteration, since calendar DOM may change after clicking.
                        for sel in [f'main [data-date="{month}-{day:02d}"]',f'main button',f'main [role="button"]',f'main a']:
                            loc=page.locator(sel)
                            if sel.endswith('"'+month+f'-{day:02d}'+'"]'):
                                targets=loc.all()
                            else:
                                targets=[e for e in loc.all() if re.match(r'^'+str(day)+r'(?:\s|$)',(e.inner_text(timeout=500) or '').strip())]
                            if targets:
                                try:
                                    targets[0].click(timeout=1200,force=False)
                                    page.wait_for_timeout(550)
                                    selected_days.add(day)
                                    break
                                except Exception:pass
                        # Site may link to direct `.pb` files in rendered match rows. Extract
                        # structured DOM attributes only; do not invent player or score fields.
                        try:
                            dom=page.evaluate('''() => [...document.querySelectorAll('[data-game-id],[data-replay-id]')].map(el=>({
                                ...el.dataset, id:el.dataset.gameId||el.dataset.replayId,
                                date:el.dataset.date, duration:el.dataset.duration,
                                map:el.dataset.map, score1:el.dataset.score1,score2:el.dataset.score2,
                                team1:el.dataset.team1,team2:el.dataset.team2
                            }))''')
                            scan(dom,series,result)
                        except Exception:pass
                    received['clicked_days']=sorted(selected_days)
                    received['matches_total_so_far']=len(result)
                    sources.append(received)
                    # Diagnostics for upstream selector updates. No screenshots of private data.
                    if series==0:
                        (DEBUG/f'site_{month}.html').write_text(page.content()[:500000],encoding='utf-8')
                except Exception as e:
                    received['error']=str(e)
                    sources.append(received)
                finally:
                    page.remove_listener('response',response_handler)
        browser.close()
    (DEBUG/'collector_summary.json').write_text(json.dumps(sources,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return result,sources


def main():
    ap=argparse.ArgumentParser(description='Merge new Altistats game records into static dataset')
    ap.add_argument('--days',type=int,default=45,help='Look back this many days on Altistats (max 75)')
    ap.add_argument('--fixture',type=Path,help='Offline test only: JSON list of complete match records')
    args=ap.parse_args()
    previous=load_existing()
    if args.fixture:
        fresh={};scan(json.loads(args.fixture.read_text()),0,fresh)
        sources=['offline fixture']
    else:
        fresh,sources=crawl(max(1,min(75,args.days)))
    if not fresh:
        print('ERROR: No full game records were extracted from Altistats. Existing data not modified.',file=sys.stderr)
        print('Check the Actions diagnostics artifact. The upstream UI or access method may require a collector adjustment.',file=sys.stderr)
        return 2
    # Reject stale extracts on a live run: prevents a successful-looking job
    # from silently refreshing a dataset that cannot reach current games.
    newest=max(r[1] for r in fresh.values())
    if not args.fixture:
        freshness=(datetime.now(timezone.utc)-datetime.fromisoformat(newest.replace('Z','+00:00'))).days
        if freshness>10:
            print('ERROR: only old games were extracted; latest upstream game is',newest,file=sys.stderr)
            return 3
    added=0
    for rid,record in fresh.items():
        if rid not in previous:
            previous[rid]=record
            added+=1
    save(previous,added,len(fresh),sources)
    print('Extracted',len(fresh),'complete games;',added,'new; total',len(previous),'; newest scraped',newest)
    return 0

if __name__=='__main__':sys.exit(main())
