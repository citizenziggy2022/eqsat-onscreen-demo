import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from datetime import datetime, timezone, timedelta
import gov_sources as g
NOW = datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc)
class R:  # fake response
    def __init__(s, t): s.text = t
    def raise_for_status(s): pass
def tg(post, when, body): return f'<div class="tgme_widget_message_wrap x"><div data-post="{post}"></div><div class="tgme_widget_message_text js-x">{body}</div><time datetime="{when}"></time></div>'
SRC = g.WATER_SOURCES[0]
class T(unittest.TestCase):
    def test_body_whole_sentences(self):
        s = ["Перше речення досить довге для тесту тут.", "Друге речення теж досить довге для тесту." * 6]
        self.assertEqual(g.body_of(s), s[0])
    def test_body_long_first_gets_ellipsis(self):
        b = g.body_of(["слово " * 100 + "кінець."]); self.assertTrue(b.endswith("…")); self.assertLessEqual(len(b), g.BODY_MAX)
    def test_expired_and_future_dropped(self):
        self.assertIsNone(g.make_item("a", "t", "b", "s", "https://x", "r", NOW - timedelta(hours=13), NOW))
        self.assertIsNone(g.make_item("a", "t", "b", "s", "https://x", "r", NOW + timedelta(hours=2), NOW))
        self.assertIsNotNone(g.make_item("a", "t", "b", "s", "https://x", "r", NOW - timedelta(hours=11), NOW))
    def test_water_keeps_water_drops_noise_and_military(self):
        page = "".join([tg("c/1", "2026-11-24T12:00:00+00:00", "Припинено водопостачання на вул. Тестовій. Роботи тривають до вечора."),
            tg("c/2", "2026-11-24T12:05:00+00:00", "Тарифи на воду змінено. Оплатіть рахунок."),
            tg("c/3", "2026-11-24T12:10:00+00:00", "Водопостачання, позиції ворога біля міста."),
            tg("c/4", "2026-11-24T01:00:00+00:00", "Водопостачання відновлено у всіх районах міста.")])
        got = g.parse_channel(page, SRC, NOW)
        self.assertEqual([i["id"] for i in got], ["gov-water-c-1"])
        self.assertTrue(got[0]["source_url"].startswith("https://t.me/c/1"))
        self.assertTrue(got[0]["channel_verified"])
    def test_unconfirmed_channel_flagged(self):
        got = g.parse_channel(tg("k/1", "2026-11-24T12:00:00+00:00", "Тиск у водопровідних мережах стабілізовано. Вода повертається."), g.WATER_SOURCES[3], NOW)
        self.assertFalse(got[0]["channel_verified"])
    def test_water_failure_shows_nothing(self):
        def bad(u): raise RuntimeError("403")
        items, log = g.water(NOW, bad); self.assertEqual(items, []); self.assertTrue(all("failed" in l for l in log))
    def test_roads_needs_licence_and_no_other_credit(self):
        art = lambda lic, extra="": f'<h1>Проїзд дорогами забезпечено</h1><article>Назад 24.11.2026 Новини Проїзд дорогами забезпечено Дороги і мости Агентство відновлення Дорожні служби працюють у посиленому режимі зараз. {extra}</article>{"Creative Commons Attribution 4.0" if lic else ""}'
        listing = '<article><a href="https://restoration.gov.ua/blog/a/"></a><time datetime="2026-11-24T10:00:00+02:00"></time></article>'
        def mk(a): return lambda u: R(listing if u.endswith("/blog/") else a)
        self.assertEqual(len(g.roads(NOW, mk(art(True)))[0]), 1)
        self.assertEqual(g.roads(NOW, mk(art(False)))[0], [])
        self.assertEqual(g.roads(NOW, mk(art(True, "Фото: Reuters")))[0], [])
    def test_roads_stale_listing_item_skipped(self):
        listing = '<article><a href="https://restoration.gov.ua/blog/a/"></a><time datetime="2026-11-20T10:00:00+02:00"></time></article>'
        self.assertEqual(g.roads(NOW, lambda u: R(listing))[0], [])
class T2(unittest.TestCase):
    def test_listing_real_markup_and_abbreviations(self):
        sec = '<section id="b" class="blog-item x"><a href="https://restoration.gov.ua/blog/q/"></a><time datetime="2026-11-24T07:57:26+02:00"></time></section>'
        self.assertEqual(g.listing(sec), [("https://restoration.gov.ua/blog/q/", "2026-11-24T07:57:26+02:00")])
        s = g.sentences("Припинено водопостачання по вул. Вадатурського від вул. Морської. Роботи тривають до 20.00.")
        self.assertEqual(len(s), 2); self.assertIn("вул. Вадатурського", s[0])
class T3(unittest.TestCase):
    def test_initials_and_interview(self):
        s = g.sentences("Припинено водопостачання по вул. Вадатурського від вул. В. Морської до вул. Адміральської. Ремонтні роботи тривають до 20.00.")
        self.assertEqual(len(s), 2)

class T4(unittest.TestCase):
    def test_timezone_expiry_exact(self):
        pub = datetime.fromisoformat("2026-11-24T07:57:26+02:00").astimezone(timezone.utc)
        it = g.make_item("a", "t", "b", "s", "https://x", "r", pub, NOW)
        self.assertEqual((it["issued_at"], it["expires_at"]), ("2026-11-24T05:57:26Z", "2026-11-24T17:57:26Z"))
    def test_unconfirmed_not_ingested_by_default(self):
        seen = []
        def get(u):
            seen.append(u); ch = u.rsplit("/", 1)[-1]
            return R(tg(ch + "/1", "2026-11-24T12:00:00+00:00", "Тиск у водопровідних мережах стабілізовано. Вода повертається."))
        items, log = g.water(NOW, get)
        self.assertEqual(seen, ["https://t.me/s/InfoxVodokanal", "https://t.me/s/kvk20"]); self.assertFalse(any(i.get("preview_only") for i in items))
        items, log = g.water(NOW, get, preview_unconfirmed=True)
        flags = {i["id"]: i.get("preview_only", False) for i in items}
        self.assertTrue(flags["gov-water-InfoxVodokanal-1"] is False and flags["gov-water-vodokanal1-1"] is True and flags["gov-water-kvk20-1"] is False and flags["gov-water-vodokanal_kharkiv-1"] is True)
    def test_street_scope_labelled(self):
        got = g.parse_channel(tg("c/9", "2026-11-24T12:00:00+00:00", "Припинено водопостачання по вул. Тестовій. Роботи до вечора тривають."), SRC, NOW)
        self.assertEqual(got[0]["scope"], "street-level"); self.assertIn("окремі вулиці", got[0]["region"])
class T6(unittest.TestCase):
    def test_long_road_title_left_for_review(self):
        long = "Дороги державного значення " * 5
        art = f'<h1>{long}</h1><article>Назад 24.11.2026 Новини {long} Дороги і мости Агентство відновлення Проїзд дорогами забезпечено у посиленому режимі зараз.</article>Creative Commons Attribution 4.0'
        listing = '<article><a href="https://restoration.gov.ua/blog/a/"></a><time datetime="2026-11-24T10:00:00+02:00"></time></article>'
        items, log = g.roads(NOW, lambda u: R(listing if u.endswith("/blog/") else art))
        self.assertEqual(items, []); self.assertTrue(any("needs review" in l for l in log))
REAL = ("🚱 Аварійне відключення води у частині Одеської області 25 листопада 2026 року!<br/><br/>Шановні споживачі! Інформуємо вас про тимчасове припинення водопостачання у зв’язку з необхідністю проведення аварійно-ремонтних робіт на водопроводі.<br/><br/>🗓 25 листопада 2026 року<br/><br/>⏰ з 10:00 до 24:00<br/><br/>📍 Водопостачання буде відсутнє: Фонтанка-3, Вапнярка, Нова Дофінівка, Чорноморське, Григорівка.<br/><br/>💧 Просимо завчасно подбати про необхідний запас води.")

class T7(unittest.TestCase):
    def test_real_planned_outage(self):
        log = []
        got = g.parse_channel(tg("InfoxVodokanal/1288", "2026-11-24T16:23:51+00:00", REAL), SRC, datetime(2026, 11, 24, 17, 0, tzinfo=timezone.utc), log)
        self.assertEqual(len(got), 1); w = got[0]
        self.assertEqual(w["starts_at"], "2026-11-25T08:00:00Z")      # 10:00 Kyiv (UTC+2)
        self.assertEqual(w["expires_at"], "2026-11-25T22:00:00Z")     # 24:00 Kyiv
        self.assertIn("Заплановано 25.11, 10:00–24:00", w["body"])
        self.assertIn("Чорноморське", w["body"]); self.assertNotIn("2026 року", w["title"])
        self.assertIn("Одеська область", w["region"]); self.assertNotEqual(w["region"].split(" ")[0], "Одеса")
    def test_unparsed_planned_goes_to_review(self):
        log = []
        bad = REAL.replace("з 10:00 до 24:00", "з ранку до вечора")
        got = g.parse_channel(tg("InfoxVodokanal/1289", "2026-11-24T16:23:51+00:00", bad), SRC, datetime(2026, 11, 24, 17, 0, tzinfo=timezone.utc), log)
        self.assertEqual(got, []); self.assertTrue(any("needs review" in l for l in log))
    def test_partial_without_places_goes_to_review(self):
        log = []
        got = g.parse_channel(tg("c/5", "2026-11-24T12:00:00+00:00", "Аварійне відключення води у частині області завтра! Інформуємо про припинення водопостачання."), SRC, NOW, log)
        self.assertEqual(got, []); self.assertTrue(log)
    def test_bad_hours_review(self):
        log = []
        bad = REAL.replace("з 10:00 до 24:00", "з 25:00 до 26:00")
        self.assertEqual(g.parse_channel(tg("c/6", "2026-11-24T16:23:51+00:00", bad), SRC, NOW, log), [])
    def test_ended_outage_dropped(self):
        got = g.parse_channel(tg("c/7", "2026-11-24T16:23:51+00:00", REAL), SRC, datetime(2026, 11, 26, 0, 0, tzinfo=timezone.utc), [])
        self.assertEqual(got, [])
class T8(unittest.TestCase):
    def test_long_water_title_goes_to_review_not_air(self):
        log = []
        long_ = "Київводоканал закликає користуватися водою ощадливо Унаслідок інтенсивних ворожих атак на критичну інфраструктуру столиці системи працюють у складних умовах. Прохання до мешканців."
        got = g.parse_channel(tg("c/6", "2026-11-24T12:00:00+00:00", long_), SRC, NOW, log)
        self.assertEqual(got, []); self.assertTrue(any("needs review" in l for l in log))
if __name__ == "__main__": unittest.main()
