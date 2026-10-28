# PlaneBall daily Discord leaderboard

This independent GitHub Actions workflow posts **all PlaneBall 4v4 players whose rank or rating points changed** to Discord. It checks Diamond, Gold, Silver and Bronze, comparing the last successful daily snapshot with the new snapshot, and highlights the biggest movers and tier leaders. Rating (R) is the leaderboard's rating score, not goals or assists.

## One-time setup
1. Discord: In the desired text channel, open **Edit Channel → Integrations → Webhooks → New Webhook**, choose the channel, and **Copy Webhook URL**.
2. GitHub: Open your repository's **Settings → Secrets and variables → Actions → New repository secret**. Name the secret **DISCORD_WEBHOOK_URL** and paste your webhook URL as its value. Never publish the secret or paste it into chat.
3. Merge the pull request into main.
4. GitHub: Open **Actions → PlaneBall daily Discord leaderboard → Run workflow** for the first test. The first run posts a baseline; subsequent runs list changes.

Schedule: **02:15 UTC daily** (8:15 p.m. Central Standard Time or 9:15 p.m. Central Daylight Time). GitHub's scheduled jobs may be delayed.

The previous successful snapshot is stored in `data/planeball_daily.json` and committed to GitHub. Season rollover starts a fresh baseline. If pages are incomplete or Discord fails, the snapshot is not advanced. A partially posted Discord multi-message report can repeat on retry.

## Tests
```sh
python -m pip install requests==2.32.5 beautifulsoup4==4.14.2
python -m unittest discover -s tests -p test_planeball_discord.py
python scripts/planeball_discord.py --dry-run
```

This workflow is independent of the existing Altistats replay collector. If PlaneBall changes its page structure, a parser update may be needed.
