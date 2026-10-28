#!/usr/bin/env python3
"""Reconstruct previous 30 days of rating history from player match records.

Ranks are estimates within the currently ranked 81-player roster, NOT an
official historical all-player ladder. The existing real snapshot is preserved.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup

ET=ZoneInfo("America/New_York")
UTC=timezone.utc
TIERS=("diamond","gold","silver","bronze")
USER_AGENT="PlaneBallHistoricalBackfill/1.0 (public player match logs, one-time)"

def get_history(pid,season):
    url=f"https://planeball.com/players/?id={pid}&mode=4BALL&season={season}"
    last_error=None
    for attempt in range(3):
        try:
            with requests.Session() as session:
                r=session.get(url,headers={"User-Agent":USER_AGENT},timeout=(20,75+attempt*40))
                r.raise_for_status()
            break
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as exc:
            last_error=exc
            if attempt<2:
                time.sleep(3*(attempt+1))
    else:
        raise RuntimeError(f"Player {pid}: three attempts exhausted: {last_error}")
    soup=BeautifulSoup(r.text,"html.parser")
    matches=[]
    found=False
    total_games=None
    for table in soup.find_all("table"):
        rows=table.find_all("tr")
        if not rows: continue
        headers=[re.sub(r"\s+","",x.get_text(" ",strip=True).upper())
                 for x in rows[0].find_all(["th","td"],recursive=False)]
        if {"#","R","W","L"}.issubset(headers):
            vals=rows[1].find_all(["th","td"],recursive=False) if len(rows)>1 else []
            if len(vals)>max(headers.index("W"),headers.index("L")):
                try:
                    total_games=int(vals[headers.index("W")].get_text(" ",strip=True))+int(
                        vals[headers.index("L")].get_text(" ",strip=True))
                except ValueError: pass
        if "ENDTIME(ET)" not in headers or "R" not in headers or "RC" not in headers:
            continue
        found=True
        di,ri,ci=(headers.index(x) for x in ("ENDTIME(ET)","R","RC"))
        for row in rows[1:]:
            vals=row.find_all(["th","td"],recursive=False)
            if len(vals)<=max(di,ri,ci): continue
            label=re.sub(r"\s+"," ",vals[di].get_text(" ",strip=True))
            if not re.fullmatch(r"\d{1,2}:\d{2}(?:AM|PM) [A-Za-z]{3} \d{1,2}, \d{2}",label):
                continue
            at=datetime.strptime(label,"%I:%M%p %b %d, %y").replace(tzinfo=ET).astimezone(UTC)
            before=vals[ri].get_text(" ",strip=True).replace(",","")
            delta=vals[ci].get_text(" ",strip=True).replace(",","").replace("−","-")
            if not re.fullmatch(r"-?\d+",before) or not re.fullmatch(r"[+-]?\d+",delta):
                raise ValueError(f"Player {pid}: invalid rating on {label}")
            matches.append((at,int(before),int(delta)))
    if not found:
        raise ValueError(f"Player {pid}: match history table missing")
    if total_games is None:
        raise ValueError(f"Player {pid}: W/L game count missing")
    matches.sort(key=lambda row:row[0],reverse=True)
    return pid,matches,total_games

def reconstruct(anchor,histories,days=30):
    """Reconstruct the same 02:35 UTC daily cutoff used by the scheduled job."""
    anchor_dt=datetime.fromisoformat(anchor["captured_at"].replace("Z","+00:00")).astimezone(UTC)
    if set(histories)!=set(anchor["players"]):
        raise ValueError("Incomplete player histories")
    if len(histories)<15: raise ValueError("Suspiciously few players")
    cutoffs=[datetime.combine(anchor_dt.date()-timedelta(days=i),datetime.min.time(),UTC)
             +timedelta(hours=2,minutes=35) for i in range(days,0,-1)]
    output=[]
    warnings=set()
    for cutoff in cutoffs:
        roster={}
        for pid,player in anchor["players"].items():
            games,total_games=histories[pid]
            after=[g for g in games if cutoff<g[0]<=anchor_dt]
            future=sum(g[0]>anchor_dt for g in games)
            # Provisional players are not ranked until their first 10 games.
            if total_games-future-len(after)<10: continue
            rating=player["rating"]
            for at,before,change in after:
                if rating!=before+change: warnings.add(pid)
                rating=before
            roster[pid]={**player,"rating":rating,"rank":None}
        for tier in TIERS:
            ranked=sorted(((pid,p) for pid,p in roster.items() if p["tier"]==tier),
                          key=lambda t:(-t[1]["rating"],t[0]))
            for number,(pid,_) in enumerate(ranked,1):
                roster[pid]["rank"]=number
        output.append({
            "season":anchor["season"],
            "captured_at":cutoff.isoformat(),
            "reconstructed":True,
            "provenance":"player match history; ratings reconstructed, ranks estimated",
            "rank_scope":"the players in current snapshot only; not an official archived rank",
            "players":roster
        })
    return output,warnings

def merge(feed,backfill):
    result=list(feed["snapshots"])
    days={s["captured_at"][:10] for s in result}
    for snapshot in backfill:
        if snapshot["captured_at"][:10] not in days:
            result.append(snapshot)
            days.add(snapshot["captured_at"][:10])
    result.sort(key=lambda s:s["captured_at"])
    return {**feed,"snapshots":result[-60:]}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--file",type=Path,default=Path("data/planeball_history.json"))
    p.add_argument("--days",type=int,default=30)
    p.add_argument("--workers",type=int,default=4)
    args=p.parse_args()
    if not 1<=args.days<=59:p.error("--days must be 1..59")
    feed=json.loads(args.file.read_text(encoding="utf-8"))
    if feed.get("format")!=1 or not feed.get("snapshots"):
        raise ValueError("No previous real snapshot; not backfilling")
    real=[s for s in feed["snapshots"] if not s.get("reconstructed")]
    if not real: raise ValueError("No real baseline snapshot")
    anchor=max(real,key=lambda s:s["captured_at"])
    histories,errors={},[]
    with ThreadPoolExecutor(max_workers=max(1,min(6,args.workers))) as pool:
        futures={pool.submit(get_history,pid,anchor["season"]):pid for pid in anchor["players"]}
        for f in as_completed(futures):
            pid=futures[f]
            try:
                got,games,total=f.result()
                histories[got]=(games,total)
            except Exception as exc:
                errors.append(f"{pid}: {exc}")
    if errors:
        raise RuntimeError(f"{len(errors)} profiles failed; no partial data saved. "+
                           " | ".join(errors[:8]))
    rebuilt,warnings=reconstruct(anchor,histories,args.days)
    merged=merge(feed,rebuilt)
    added=len(merged["snapshots"])-len(feed["snapshots"])
    if added<=0:
        print("No new historical days to add")
        return
    print(f"Backfilled {added} daily dates for {len(histories)} players; "+
          f"{len(merged['snapshots'])} snapshots total.")
    print(f"Players with any source rating discontinuity (rating correction/reordering): {len(warnings)}")
    if warnings: print("Affected player IDs: "+",".join(sorted(warnings)))
    tmp=args.file.with_suffix(".tmp")
    tmp.write_text(json.dumps(merged,ensure_ascii=False,separators=(",",":"))+"\n",encoding="utf-8")
    tmp.replace(args.file)

if __name__=="__main__":
    main()
