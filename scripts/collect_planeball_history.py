#!/usr/bin/env python3
"""Collect public 4v4 PlaneBall rankings into a rolling daily JSON history.

The Cloudflare frontend reads this file directly from raw.githubusercontent.com.
No Discord webhook, Cloudflare token, or private backend is needed.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

LADDER = "https://planeball.com/ranked/4v4/"
TIERS = ("diamond", "gold", "silver", "bronze")
USER_AGENT = "PlaneBallRankHistory/1.0 (public ranked ladder; daily snapshot)"


def get(session, url):
    response = session.get(url, timeout=35, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response


def season_number(session):
    response = get(session, LADDER)
    match = re.search(r"/ranked/4v4/season-(\d+)/", response.url)
    if not match:
        text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
        match = re.search(r"4v4\s+(?:Ball\s+)?(?:Ranked\s+)?(?:Ladder\s+[–—-]\s+)?Season\s+(\d+)", text, re.I)
    if not match:
        raise ValueError("Unable to identify active season")
    return int(match.group(1))


def parse_tier(html, tier, season):
    soup = BeautifulSoup(html, "html.parser")
    players = {}
    found = False
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        labels = [re.sub(r"\s+", "", th.get_text(" ", strip=True).lower())
                  for th in rows[0].find_all(["td", "th"], recursive=False)]
        if not {"nickname", "r", "aka"}.issubset(labels):
            continue
        found = True
        indices = {key: labels.index(key) for key in ("nickname", "r", "aka")}
        indices["rank"] = labels.index("#") if "#" in labels else 0
        for row in rows[1:]:
            cells = row.find_all(["td", "th"], recursive=False)
            if len(cells) <= max(indices.values()):
                continue
            rank = cells[indices["rank"]].get_text(" ", strip=True)
            rating = cells[indices["r"]].get_text(" ", strip=True).replace(",", "")
            if not re.fullmatch(r"\d+", rank) or not re.fullmatch(r"-?\d+", rating):
                continue
            nickname_cell = cells[indices["nickname"]]
            anchor = next((a for a in nickname_cell.select("a[href]")
                           if parse_qs(urlparse(a["href"]).query).get("id")), None)
            if not anchor:
                raise ValueError(f"Missing player id in {tier} rank {rank}")
            url = urljoin(LADDER, anchor["href"])
            player_id = parse_qs(urlparse(url).query)["id"][0]
            if not player_id.isdigit() or player_id in players:
                raise ValueError(f"Invalid or duplicate player id in {tier}: {player_id!r}")
            players[player_id] = {
                "name": anchor.get_text(" ", strip=True)
                        or nickname_cell.get_text(" ", strip=True),
                "aka": cells[indices["aka"]].get_text(" ", strip=True),
                "tier": tier,
                "rank": int(rank),
                "rating": int(rating),
                "url": url,
            }
    if not found or not players:
        raise ValueError(f"Missing/empty {tier} leaderboard, season {season}")
    return players


def collect(session):
    season = season_number(session)
    players = {}
    for tier in TIERS:
        url = f"{LADDER}season-{season}/{tier}/"
        tier_players = parse_tier(get(session, url).text, tier, season)
        for player_id, player in tier_players.items():
            if player_id in players:
                raise ValueError(f"Player {player_id} duplicated across tiers")
            players[player_id] = player
    if len(players) < 15:
        raise ValueError(f"Suspiciously small ladder ({len(players)} players)")
    return {
        "season": season,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "players": players,
    }


def update_history(existing, current, keep=60):
    history = existing.get("snapshots", []) if isinstance(existing, dict) else []
    if not isinstance(history, list):
        raise ValueError("Invalid stored snapshots")
    for snapshot in history:
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("players"), dict):
            raise ValueError("Invalid previous snapshot")
    if history and history[-1].get("season") == current["season"]:
        previous = history[-1]["players"]
        for tier in TIERS:
            old_count = sum(p.get("tier") == tier for p in previous.values())
            new_count = sum(p.get("tier") == tier for p in current["players"].values())
            if old_count >= 10 and new_count < old_count * .8:
                raise ValueError(f"{tier} shrank unexpectedly ({old_count} -> {new_count})")
    day = current["captured_at"][:10]
    history = [s for s in history if s.get("captured_at", "")[:10] != day]
    history.append(current)
    history.sort(key=lambda s: s["captured_at"])
    return {"format": 1, "source": LADDER, "snapshots": history[-keep:]}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/planeball_history.json"))
    parser.add_argument("--keep", type=int, default=60)
    args = parser.parse_args(argv)
    if args.keep < 2:
        parser.error("--keep must be >= 2")
    prior = json.loads(args.output.read_text(encoding="utf-8")) if args.output.exists() else {}
    with requests.Session() as session:
        current = collect(session)
    feed = update_history(prior, current, args.keep)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(".tmp")
    temp.write_text(json.dumps(feed, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temp.replace(args.output)
    print(f"Recorded {len(current['players'])} players in season {current['season']}; "
          f"history has {len(feed['snapshots'])} snapshots")
    return 0


if __name__ == "__main__":
    sys.exit(main())
