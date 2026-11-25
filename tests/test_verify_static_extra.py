"""Offline tests for the family/AM rules (no network). Run: python3 -m unittest tests/test_verify_static_extra.py"""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import verify_static as v

PAGES = {}
def fake_fetch(url):
    p = PAGES.get(url)
    return (p.encode(), "") if isinstance(p, str) else (None, p or "HTTP 403")

NIB = "<p>Національне інформаційне бюро ГАРЯЧА ЛІНІЯ ЦІЛОДОБОВО: 1648 +380 44 287-81-65 дзвінки з-за кордону</p>"
RADIO = "<p>Українське радіо. AM частоти: 1278 кГц (цілодобово) та 1386 кГц (з 00:00 до 6:30 в Україні</p>"

def ln(number, en, uk="", url=""): return {"number": number, "en": en, "uk": uk, "source_url": url}

class T(unittest.TestCase):
    def setUp(self):
        PAGES.clear(); self._f = v.fetch; v.fetch = fake_fetch
    def tearDown(self):
        v.fetch = self._f

    def test_nib_hotline_and_abroad(self):
        PAGES["https://nib.gov.ua/"] = NIB
        self.assertEqual(v.check_line("family", ln("1648", "National Information Bureau, 24/7"))[0], "confirmed")
        self.assertEqual(v.check_line("family", ln("+380 44 287 81 65", "NIB, from abroad"))[0], "confirmed")

    def test_nib_changed_number_unverified(self):
        PAGES["https://nib.gov.ua/"] = NIB.replace("1648", "1649").replace("287-81-65", "287-00-00")
        self.assertEqual(v.check_line("family", ln("1648", "National Information Bureau, 24/7"))[0], "unverified")
        self.assertEqual(v.check_line("family", ln("+380 44 287 81 65", "NIB, from abroad"))[0], "unverified")

    def test_blocked_is_failed(self):
        self.assertEqual(v.check_line("family", ln("1648", "National Information Bureau, 24/7"))[0], "failed")

    def test_wrong_card_or_label_gets_no_rule(self):
        PAGES["https://nib.gov.ua/"] = NIB
        self.assertEqual(v.check_line("family", ln("1648", "Something else"))[0], "unverified")

    def test_icrc_and_missing_persons(self):
        PAGES["https://www.icrc.org/en/document/central-tracing-agency-missing-persons-ukraine"] = "Central Tracing Agency ... Call the ICRC In Ukraine: 0 800 300 155 (toll-free number)"
        self.assertEqual(v.check_line("family", ln("0 800 300 155", "ICRC tracing, Mon–Fri"))[0], "confirmed")
        u = [k for k in v.CONTACT_RULES if k == ("family", "1698")][0]
        PAGES[v.CONTACT_RULES[u][0]] = "Гаряча лінія Уповноваженого з питань осіб, зниклих безвісти ... за коротким номером: 1698 . Кожне"
        self.assertEqual(v.check_line("family", ln("1698", "Missing persons, Mon–Fri"))[0], "confirmed")

    def test_am_lines(self):
        PAGES["https://ukr.radio/maps"] = RADIO
        a = ln("1278 kHz AM", "Ukrainian Radio, 24 h", url="https://ukr.radio/maps")
        b = ln("1386 kHz AM", "Ukrainian Radio, night", url="https://ukr.radio/maps")
        self.assertEqual(v.check_line("radio-am-sw", a)[0], "confirmed")
        self.assertEqual(v.check_line("radio-am-sw", b)[0], "confirmed")
        PAGES["https://ukr.radio/maps"] = RADIO.replace("1386 кГц (з 00:00 до 6:30", "1386 кГц (з 01:00 до 5:00")
        self.assertEqual(v.check_line("radio-am-sw", b)[0], "unverified")

    def test_hand_entered_line_stays_unverified(self):
        r = v.check_line("family", ln("nib.gov.ua · redcross.org.ua/rfl · rfl@redcross.org.ua", ""))
        self.assertEqual(r[0], "unverified")

if __name__ == "__main__": unittest.main()
