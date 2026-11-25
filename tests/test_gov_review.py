"""Offline tests. Run: python3 -m unittest tests/test_gov_review.py from the repo root."""
import hashlib, json, os, sys, tempfile, unittest
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import fetch_gov_review as r

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
def item(i, exp, iss="2026-11-25T06:00:00Z"):
    return {"id": i, "title": "t", "body": "b", "source": "s", "source_url": "https://x", "issued_at": iss, "expires_at": exp}

class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.out = os.path.join(self.d, "p.json"); self.st = os.path.join(self.d, "st.json")
        self.o = (r.g.roads, r.g.water)
    def tearDown(self):
        r.g.roads, r.g.water = self.o
    def go(self, **k):
        return r.run(now=NOW, out=self.out, state=self.st, stage=False, **k)
    def test_writes_candidates_not_on_air(self):
        r.g.roads = lambda now=None: ([item("road", "2026-11-25T18:00:00Z")], [])
        r.g.water = lambda now=None: ([item("water", "2026-11-25T22:00:00Z")], ["needs review (x)", "skip topic: y"])
        self.assertEqual(self.go(), "written")
        d = json.load(open(self.out))
        self.assertEqual([i["id"] for i in d["items"]], ["road", "water"] if d["items"][0]["id"] == "road" else ["water", "road"])
        self.assertTrue(all(i["on_air"] is False and i["status"] == "pending_review" for i in d["items"]))
        self.assertEqual(d["needs_review"], ["needs review (x)"]); self.assertEqual(d["auto_approved"], 0)
    def test_never_touches_on_air_files(self):
        names = ("auto_bulletins.json", "bulletins.json", "config.json"); cwd = os.getcwd(); os.chdir(self.d); self.addCleanup(os.chdir, cwd)
        for n in names: open(n, "w").write('{"keep":1}')
        h = {n: hashlib.sha1(open(n, "rb").read()).hexdigest() for n in names}
        r.g.roads = lambda now=None: ([item("road", "2026-11-25T18:00:00Z")], []); r.g.water = lambda now=None: ([], [])
        r.run(now=NOW, stage=False, state=self.st)
        self.assertEqual(h, {n: hashlib.sha1(open(n, "rb").read()).hexdigest() for n in names})
    def test_expired_dropped_and_failure_keeps_last_file(self):
        json.dump({"items": [item("old", "2026-11-25T11:00:00Z"), item("keep", "2026-11-25T20:00:00Z")], "needs_review": []}, open(self.out, "w"))
        r.g.roads = lambda now=None: ([], []); r.g.water = lambda now=None: ([], [])
        self.go(); self.assertEqual([i["id"] for i in json.load(open(self.out))["items"]], ["keep"])
        before = open(self.out).read(); os.remove(self.st)
        def boom(now=None): raise RuntimeError("down")
        r.g.roads = boom
        self.assertEqual(self.go(), "failed"); self.assertEqual(open(self.out).read(), before)
    def test_throttle(self):
        r.g.roads = lambda now=None: ([], []); r.g.water = lambda now=None: ([], [])
        self.go(); self.assertEqual(self.go(), "throttled")
if __name__ == "__main__": unittest.main()
