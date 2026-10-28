import unittest
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path

module_path = Path(__file__).resolve().parents[1] / "scripts" / "planeball_discord.py"
spec = spec_from_file_location("planeball_discord", module_path)
app = module_from_spec(spec)
spec.loader.exec_module(app)

HTML = """<table><tr><th>#</th><th>Nickname</th><th>AKA</th><th>R</th></tr>
<tr><td>1</td><td><a href="/players/?id=781">XX2</a></td><td>@XX2</td><td>3185</td></tr>
<tr><td>2</td><td><a href="/players/?id=543">Guss</a></td><td>@Guss</td><td>3115</td></tr></table>"""

class TestReporter(unittest.TestCase):
    def setUp(self):
        self.players = app.parse_tier_page(HTML, "diamond", 18)
        self.old_players = {k: dict(v) for k, v in self.players.items()}
        self.old = {"season": 18, "captured_at": "2026-10-27T02:15:00+00:00", "players": self.old_players}
        self.now = {"season": 18, "captured_at": "2026-10-28T02:15:00+00:00", "players": self.players}

    def test_parser(self):
        self.assertEqual(len(self.players), 2)
        self.assertEqual(self.players["781"]["rating"], 3185)
        self.assertEqual(self.players["543"]["aka"], "@Guss")

    def test_rank_and_points(self):
        self.old_players["781"]["rank"] = 2
        self.old_players["781"]["rating"] = 3100
        report = app.compare(self.old, self.now)
        self.assertEqual(len(report["changes"]), 1)
        self.assertEqual(report["changes"][0]["rank_up"], 1)
        self.assertEqual(report["changes"][0]["delta"], 85)
        messages = "\n".join(app.build_messages(self.now, report))
        self.assertIn("+85", messages)
        self.assertIn("ALL PLAYER CHANGES", messages)

    def test_rank_only(self):
        self.old_players["781"]["rank"] = 3
        report = app.compare(self.old, self.now)
        self.assertEqual(report["changes"][0]["delta"], 0)

    def test_tier_move(self):
        self.old_players["781"]["tier"] = "gold"
        report = app.compare(self.old, self.now)
        self.assertIn("Gold #1 → Diamond #1", app.format_change(report["changes"][0]))

    def test_new_and_missing(self):
        self.old_players.pop("781")
        self.old_players["999"] = {**self.players["781"], "name": "Missing"}
        report = app.compare(self.old, self.now)
        self.assertEqual(len(report["exits"]), 1)
        self.assertEqual(len(report["changes"]), 1)

    def test_season_reset(self):
        self.now["season"] = 19
        self.assertTrue(app.compare(self.old, self.now)["baseline"])

    def test_message_chunking(self):
        lines = ["Player"+str(i)+"x"*80 for i in range(300)]
        msgs = list(app.split_lines("Header\n", lines))
        self.assertGreater(len(msgs), 1)
        self.assertTrue(all(len(s)<=1800 for s in msgs))
        self.assertTrue(all(any(line in msg for msg in msgs) for line in lines))

    def test_escape_mentions(self):
        self.assertNotIn("\n", app.safe("@everyone\nfoo"))

if __name__ == "__main__":
    unittest.main()
