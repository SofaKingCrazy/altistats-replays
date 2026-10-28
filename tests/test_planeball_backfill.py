import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import unittest

source=Path(__file__).resolve().parents[1]/"scripts"/"backfill_planeball_history.py"
spec=importlib.util.spec_from_file_location("pb_backfill",source)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.anchor={"season":18,"captured_at":"2026-10-28T18:51:46+00:00","players":{}}
        self.histories={}
        for i in range(20):
            pid=str(i)
            self.anchor["players"][pid]={"name":pid,"tier":"silver","rating":1000+i*10,
                                         "rank":20-i,"url":""}
            self.histories[pid]=([
                (datetime(2026,10,27,18,30,tzinfo=timezone.utc),990+i*10,10)
            ],50)

    def test_rewind_and_estimated_rank(self):
        older,warnings=m.reconstruct(self.anchor,self.histories,30)
        self.assertEqual(len(older),30)
        self.assertEqual(older[-1]["players"]["19"]["rating"],1180)
        self.assertEqual(older[-1]["players"]["19"]["rank"],1)
        self.assertTrue(older[-1]["reconstructed"])
        self.assertEqual(len(warnings),0)

    def test_real_dates_are_preserved(self):
        feed={"format":1,"snapshots":[
            {"captured_at":"2026-10-28T18:00:00+00:00","players":{}}]}
        older=[{"captured_at":"2026-10-27T02:35:00+00:00","players":{}},
               {"captured_at":"2026-10-28T02:35:00+00:00","players":{}}]
        merged=m.merge(feed,older)
        self.assertEqual(len(merged["snapshots"]),2)
        self.assertEqual(merged["snapshots"][-1]["captured_at"],"2026-10-28T18:00:00+00:00")

    def test_new_provisional_missing_from_old_snapshots(self):
        self.histories["0"]=(self.histories["0"][0],10)
        old,_=m.reconstruct(self.anchor,self.histories,2)
        self.assertNotIn("0",old[0]["players"])

if __name__=="__main__":
    unittest.main()
