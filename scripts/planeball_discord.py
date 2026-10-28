#!/usr/bin/env python3
"""Report *every* daily PlaneBall 4v4 ladder change to a Discord webhook.

The source provides tier-specific ranks and rating (R) points. Player IDs are
stable across nickname changes. No Discord token is committed to source.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urljoin, urlparse, parse_qs
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

LADDER = "https://planeball.com/ranked/4v4/"
TIERS = ("diamond", "gold", "silver", "bronze")
UA = "PlaneBallDailyDiscordReporter/1.0 (public leaderboard; once per day)"
CENTRAL = ZoneInfo("America/Chicago")


def fetch(session, url):
    response = session.get(url, timeout=35, headers={
        "User-Agent": UA, "Cache-Control": "no-cache"})
    response.raise_for_status()
    return response


def current_season(session):
    """Find active season from the 4v4 landing page, never assume a season."""
    response = fetch(session, LADDER)
    match = re.search(r"/ranked/4v4/season-(\d+)/", response.url)
    if not match:
        soup = BeautifulSoup(response.text, "html.parser")
        for heading in soup.find_all(["h1", "h2", "h3"]):
            match = re.search(r"\bSeason\s+(\d+)\b", heading.get_text(" ", strip=True), re.I)
            if match:
                break
    if not match:
        raise RuntimeError("Could not identify current 4v4 season; will not post stale results")
    return int(match.group(1))


def canonical_header(value):
    return re.sub(r"\s+", "", value.strip().lower())


def parse_tier_page(html, tier, season):
    """Parse the complete tier detail page, not the home page's top-three list."""
    soup = BeautifulSoup(html, "html.parser")
    results = {}
    table_found = False
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header_cells = rows[0].find_all(["th", "td"], recursive=False)
        headers = [canonical_header(c.get_text(" ", strip=True)) for c in header_cells]
        if not {"nickname", "aka", "r"}.issubset(headers):
            continue
        table_found = True
        name_idx = headers.index("nickname")
        aka_idx = headers.index("aka")
        rating_idx = headers.index("r")
        rank_idx = headers.index("#") if "#" in headers else 0
        for row in rows[1:]:
            cells = row.find_all(["td", "th"], recursive=False)
            if len(cells) <= max(name_idx, aka_idx, rating_idx, rank_idx):
                continue
            rank_text = cells[rank_idx].get_text(" ", strip=True)
            rating_text = cells[rating_idx].get_text(" ", strip=True).replace(",", "")
            if not re.fullmatch(r"\d+", rank_text) or not re.fullmatch(r"-?\d+", rating_text):
                continue
            name_cell = cells[name_idx]
            anchor = next((a for a in name_cell.find_all("a", href=True)
                           if "id=" in a["href"]), None)
            if anchor is None:
                raise RuntimeError(f"Missing player ID in {tier} row #{rank_text}")
            url = urljoin(LADDER, anchor["href"])
            pid = parse_qs(urlparse(url).query).get("id", [None])[0]
            if not pid or not pid.isdigit():
                raise RuntimeError(f"Invalid player ID on {tier} row #{rank_text}")
            if pid in results:
                raise RuntimeError(f"Duplicate player ID {pid} on {tier} leaderboard")
            results[pid] = {
                "name": anchor.get_text(" ", strip=True) or name_cell.get_text(" ", strip=True),
                "aka": cells[aka_idx].get_text(" ", strip=True),
                "tier": tier,
                "rank": int(rank_text),
                "rating": int(rating_text),
                "url": url,
            }
    if not table_found or not results:
        raise RuntimeError(f"No valid rows on {tier} leaderboard for season {season}")
    return results


def collect(session, previous=None, now=None):
    season = current_season(session)
    players, tier_counts = {}, {}
    for tier in TIERS:
        tier_url = f"{LADDER}season-{season}/{tier}/"
        fresh = parse_tier_page(fetch(session, tier_url).text, tier, season)
        if players.keys() & fresh.keys():
            raise RuntimeError(f"Duplicate player IDs across tiers (including {tier})")
        players.update(fresh)
        tier_counts[tier] = len(fresh)
    if len(players) < 12:
        raise RuntimeError("Unexpectedly few players; refusing a partial snapshot")
    # An incomplete fetch could look like dozens of players disappearing.
    # Reject suspiciously large losses in a single tier during the SAME season.
    if previous and previous.get("season") == season:
        for tier in TIERS:
            old_count = sum(p["tier"] == tier for p in previous["players"].values())
            if old_count >= 10 and tier_counts[tier] < old_count * 0.8:
                raise RuntimeError(
                    f"{tier}: {tier_counts[tier]} players vs previous {old_count}; "
                    "refusing possibly incomplete snapshot")
    return {
        "season": season,
        "captured_at": (now or datetime.now(timezone.utc)).isoformat(),
        "players": players,
    }


def load_previous(path):
    if not path.exists():
        return None
    prior = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(prior, dict) or not isinstance(prior.get("players"), dict):
        raise RuntimeError("Snapshot is invalid; refusing comparison")
    return prior


def compare(previous, current):
    if not previous or previous.get("season") != current["season"]:
        return {"baseline": True, "changes": [], "exits": [], "previous_at": None}
    changes = []
    for pid, now in current["players"].items():
        old = previous["players"].get(pid)
        if old is None:
            changes.append({"id": pid, "now": now, "old": None,
                            "delta": None, "rank_up": None})
            continue
        delta = now["rating"] - old["rating"]
        rank_up = old["rank"] - now["rank"] if old["tier"] == now["tier"] else None
        if delta or rank_up or now["tier"] != old["tier"]:
            changes.append({"id": pid, "now": now, "old": old,
                            "delta": delta, "rank_up": rank_up})
    exits = [p for pid, p in previous["players"].items() if pid not in current["players"]]
    return {"baseline": False, "changes": changes, "exits": exits,
            "previous_at": previous.get("captured_at")}


def safe(s):
    """Escape Discord markdown, newlines and potential display formatting."""
    s = re.sub(r"[\r\n\t]", " ", str(s or ""))
    return re.sub(r"([\\*_~`|>])", r"\\\1", s)[:85]


def sign(n):
    return f"{n:+,}"


def nickname(p):
    return safe(p.get("aka") or p["name"])


def rank_text(p):
    return f"{p['tier'].title()} #{p['rank']}"


def format_change(c):
    now, old = c["now"], c["old"]
    if old is None:
        return f"🆕 **{nickname(now)}**: {rank_text(now)} | {now['rating']:,} R (new)"
    delta = c["delta"]
    emoji = "🟢" if delta > 0 else "🔴" if delta < 0 else "🔹"
    rank = f"{rank_text(old)} → {rank_text(now)}"
    if c["rank_up"]:
        rank += f" ({sign(c['rank_up'])} ranks)"
    return (f"{emoji} **{nickname(now)}**: {rank} | "
            f"{old['rating']:,}→{now['rating']:,} R ({sign(delta)})")


def local_date(iso):
    if not iso:
        return "unknown"
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(CENTRAL).strftime("%b %d, %Y")


def split_lines(header, lines, max_len=1800):
    """Ensure messages are below Discord's 2,000 character limit."""
    part = header
    for line in lines:
        line = line[:max_len - 50]
        if len(part) + len(line) + 1 > max_len:
            if part.strip():
                yield part.rstrip()
            part = ""
        part += line + "\n"
    if part.strip():
        yield part.rstrip()


def build_messages(current, report):
    date = local_date(current["captured_at"])
    season = current["season"]
    title = f"🏆 **PlaneBall 4v4 Daily Rankings — Season {season}**\n📅 **{date} (Central)**\n"
    if report["baseline"]:
        return [title + f"First snapshot: **{len(current['players'])} ranked players**. "
                "The next report will show *all* players whose rank or rating changes.\n" + LADDER]

    changes = report["changes"]
    measured = [c for c in changes if c["old"] is not None]
    gains = sorted((c for c in measured if c["delta"] > 0),
                   key=lambda c: c["delta"], reverse=True)
    losses = sorted((c for c in measured if c["delta"] < 0),
                    key=lambda c: c["delta"])
    ranks_up = sum(c["rank_up"] is not None and c["rank_up"] > 0 for c in measured)
    ranks_down = sum(c["rank_up"] is not None and c["rank_up"] < 0 for c in measured)
    new_count = sum(c["old"] is None for c in changes)

    lead = (title + f"Compared with **{local_date(report['previous_at'])}**\n"
            f"📊 **{len(changes)} changed/new players** | {len(gains)} gained R | "
            f"{len(losses)} lost R | {new_count} new\n"
            f"📈 Rank climbs: {ranks_up} | 📉 Rank drops: {ranks_down} | "
            f"Departed: {len(report['exits'])}\n")
    if gains:
        lead += "**📈 Biggest point gainers:** " + ", ".join(
            f"{nickname(c['now'])} {sign(c['delta'])}" for c in gains[:3]) + " R\n"
    if losses:
        lead += "**📉 Biggest point losses:** " + ", ".join(
            f"{nickname(c['now'])} {sign(c['delta'])}" for c in losses[:3]) + " R\n"

    # A tier-specific ladder cannot have a meaningful cross-tier global top-five.
    # Show three leaders in EACH tier instead, as PlaneBall does on its front page.
    lead += "**👑 Current tier leaders**\n"
    for tier in TIERS:
        current_tier = sorted((p for p in current["players"].values() if p["tier"] == tier),
                              key=lambda p: (p["rank"], -p["rating"]))
        leaders = ", ".join(f"#{p['rank']} {nickname(p)} ({p['rating']:,} R)" for p in current_tier[:3])
        lead += f"{tier.title()}: {leaders}\n"
    lead += LADDER
    messages = list(split_lines("", lead.splitlines()))
    if changes:
        messages.append("📋 **ALL PLAYER CHANGES — rank and rating (R)**")
        for tier in TIERS:
            rows = sorted((c for c in changes if c["now"]["tier"] == tier),
                          key=lambda c: (c["now"]["rank"], nickname(c["now"]).lower()))
            if rows:
                messages.extend(split_lines(f"**{tier.upper()}**\n", [format_change(c) for c in rows]))
    else:
        messages.append("No rank or rating changes since the previous snapshot.")
    if report["exits"]:
        lines = [f"– **{nickname(p)}** ({rank_text(p)}, {p['rating']:,} R)"
                 for p in sorted(report["exits"], key=lambda p: (TIERS.index(p["tier"]), p["rank"]))]
        messages.extend(split_lines("**NO LONGER LISTED**\n", lines))
    return messages


def send_discord(session, webhook, messages):
    if not re.fullmatch(r"https://(?:discord\.com|discordapp\.com)/api(?:/v\d+)?/webhooks/\d+/[\w-]+", webhook):
        raise RuntimeError("Set a valid DISCORD_WEBHOOK_URL in GitHub Actions secrets")
    for msg in messages:
        if len(msg) > 2000:
            raise RuntimeError("Internal error: Discord message exceeds 2,000 characters")
        payload = {"username": "PlaneBall Daily Ladder", "content": msg,
                   "allowed_mentions": {"parse": []}}
        for attempt in range(5):
            # wait=true makes Discord return a confirmed message rather than only 204.
            resp = session.post(webhook, params={"wait": "true"}, json=payload, timeout=35)
            if resp.status_code == 429:
                time.sleep(min(max(float(resp.json().get("retry_after", 2)), 1), 30))
                continue
            resp.raise_for_status()
            if not resp.json().get("id"):
                raise RuntimeError("Discord did not confirm the message was created")
            break
        else:
            raise RuntimeError("Discord rate limit persisted; snapshot NOT advanced")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path("data/planeball_daily.json"))
    parser.add_argument("--dry-run", action="store_true",
                        help="Show formatted report without sending or advancing snapshot")
    args = parser.parse_args(argv)
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not args.dry_run and not webhook:
        parser.error("Create a DISCORD_WEBHOOK_URL GitHub Actions secret first")

    with requests.Session() as session:
        previous = load_previous(args.state)
        current = collect(session, previous=previous)
        report = compare(previous, current)
        messages = build_messages(current, report)
        for msg in messages:
            print(msg, "\n---", flush=True)
        if args.dry_run:
            return 0
        send_discord(session, webhook, messages)

    args.state.parent.mkdir(parents=True, exist_ok=True)
    temp = args.state.with_suffix(".tmp")
    temp.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(args.state)
    print(f"Posted {len(messages)} Discord messages; saved {len(current['players'])} players")
    return 0


if __name__ == "__main__":
    sys.exit(main())
