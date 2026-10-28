import importlib.util
import unittest
from pathlib import Path

P = Path(__file__).resolve().parents[1] / 'scripts' / 'collect_planeball_history.py'
spec = importlib.util.spec_from_file_location('feed', P)
feed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feed)

HTML = '''<table><tr><th>#</th><th>Nickname</th><th>AKA</th><th></th><th>R</th></tr>
<tr><td>1</td><td><a href="/players/?id=781&amp;mode=4BALL&amp;season=18">XX2</a>781</td><td>@XX2</td><td></td><td>3,185</td></tr>
<tr><td>2</td><td><a href="/players/?id=543">Guss</a>543</td><td>@Guss</td><td></td><td>3115</td></tr></table>'''

class HistoryTests(unittest.TestCase):
    def test_tier_parse(self):
        out = feed.parse_tier(HTML, 'diamond', 18)
        self.assertEqual(out['781']['rating'], 3185)
        self.assertEqual(out['543']['aka'], '@Guss')
        self.assertTrue(out['781']['url'].endswith('season=18'))

    def test_missing_table_fails(self):
        with self.assertRaises(ValueError):
            feed.parse_tier('<p>Not found</p>', 'gold', 18)

    def test_updating_same_day_replaces(self):
        snap = {'captured_at': '2026-10-28T02:00:00+00:00',
                'season': 18, 'players': {'781': {'tier':'diamond','rank':1,'rating':3000}}}
        history = feed.update_history({}, snap)
        newer = {**snap, 'captured_at':'2026-10-28T03:00:00+00:00'}
        history2 = feed.update_history(history, newer)
        self.assertEqual(len(history2['snapshots']), 1)

    def test_season_rollover(self):
        s1 = {'captured_at':'2026-10-27T00:00:00+00:00',
              'season':18,'players':{'1':{'tier':'diamond'}}}
        s2 = {'captured_at':'2026-10-28T00:00:00+00:00',
              'season':19,'players':{'2':{'tier':'gold'}}}
        self.assertEqual(len(feed.update_history(feed.update_history({}, s1), s2)['snapshots']),2)

    def test_keep_history(self):
        existing = {}
        for day in range(1, 6):
            snap={'captured_at':f'2026-10-{day:02d}T00:00:00+00:00',
                  'season':18,'players':{'1':{'tier':'diamond'}}}
            existing=feed.update_history(existing,snap,keep=3)
        self.assertEqual(len(existing['snapshots']),3)

if __name__=='__main__':
    unittest.main()
