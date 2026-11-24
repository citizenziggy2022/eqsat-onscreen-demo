"""Offline tests (no network). Run: python3 -m unittest tests/test_verify_static_nov24.py from the repo root."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import verify_static as v

PAGES = {}
def fake_fetch(url):
    p = PAGES.get(url)
    return (p.encode(), "") if isinstance(p, str) else (None, p or "HTTP 403")

class T(unittest.TestCase):
    def setUp(self):
        PAGES.clear(); self._f = v.fetch; v.fetch = fake_fetch
    def tearDown(self):
        v.fetch = self._f

    def line(self, en, uk, number): return {"en": en, "uk": uk, "number": number}

    def test_blocked_primary_is_failed_not_confirmed(self):
        r = v.check_line("MOLDOVA · TRANSNISTRIA", self.line("Consular section", "Консульський відділ", "+373 22 58 22 84"))
        self.assertEqual(r[0], "failed"); self.assertIn("403", r[2])

    def test_moldova_consular_confirmed(self):
        PAGES["https://moldova.mfa.gov.ua/"] = "<p>Посольство України в Республіці Молдова</p><p>КОНСУЛЬСЬКИЙ ВІДДІЛ</p><li>(+373) 22-58-22-84 (Консультації)</li>"
        r = v.check_line("MOLDOVA · TRANSNISTRIA", self.line("Consular section", "Консульський відділ", "+373 22 58 22 84"))
        self.assertEqual(r[0], "confirmed")

    def test_number_changed_is_unverified_not_confirmed(self):
        PAGES["https://moldova.mfa.gov.ua/"] = "<p>Посольство України в Республіці Молдова</p><p>КОНСУЛЬСЬКИЙ ВІДДІЛ</p><li>(+373) 22-58-99-99</li>"
        r = v.check_line("MOLDOVA · TRANSNISTRIA", self.line("Consular section", "Консульський відділ", "+373 22 58 22 84"))
        self.assertEqual(r[0], "unverified")

    def test_ombudsman_messenger_number(self):
        u = "https://ombudsman.gov.ua/uk/news_details/41-ukrayinec-povernuvsya-z-okupaciyi-cherez-volin"
        PAGES[u] = "<p>Уповноважений Верховної Ради України з прав людини</p><p>з тимчасово окупованих територій: +38 093 406 80 17 — WhatsApp, Viber, Telegram</p>"
        r = v.check_line("FROM OCCUPIED AREAS OR ABROAD", self.line("Ombudsman · messengers", "Омбудсман · месенджери", "+380 93 406 80 17"))
        self.assertEqual(r[0], "confirmed")

    def test_coordhq_falls_back_only_when_primary_blocked(self):
        ln = self.line("POW & missing: Coordination HQ", "Коордштаб: полонені, зниклі", "0 800 300 529")
        fb = "https://www.ombudsman.gov.ua/uk/voyennij-stan/koordinacijnij-shtab-z-pitan-povodzhennya-z-vijskovopolonenimi"
        PAGES[fb] = "Координаційний штаб з питань поводження з військовополоненими ... Спаська, 37. Телефон: 0 800 300 529 Записатися"
        r = v.check_line("HOTLINES", ln)
        self.assertEqual(r[0], "confirmed"); self.assertEqual(r[1], fb); self.assertIn("blocked", r[3])
        # primary loads but lacks the number: unverified, fallback is NOT consulted
        PAGES["https://koordshtab.gov.ua"] = "Координаційний штаб з питань поводження з військовополоненими 0 800 111 222"
        r = v.check_line("HOTLINES", ln)
        self.assertEqual(r[0], "unverified"); self.assertEqual(r[1], "https://koordshtab.gov.ua")

    def test_112_fallback_and_101_stays_failed(self):
        PAGES["https://mvs.gov.ua/mvs-specprojekti/sluzba-112-usi-ekstreni-sluzbi-za-jedinim-nomerom"] = "112 - це номер для звернення громадян, які опинилися у надзвичайній ситуації. 101, 102, 103, 104"
        r = v.check_line("EMERGENCY NUMBERS · UKRAINE", self.line("All services", "Усі служби", "112"))
        self.assertEqual(r[0], "confirmed"); self.assertIn("fallback", r[3])
        r = v.check_line("EMERGENCY NUMBERS · UKRAINE", self.line("Rescue / fire", "Рятувальники / пожежа (ДСНС)", "101"))
        self.assertEqual(r[0], "failed")   # a bare list of 101-104 never binds a number to its service

    def test_moldova_112_own_rule(self):
        PAGES["https://112.md/"] = "SERVICIUL NAȚIONAL UNIC PENTRU APELURILE DE URGENȚĂ 112 AL REPUBLICII MOLDOVA (+373) 22 294 813 ... 112 este numărul unic pentru apelurile de urgență activ"
        ln = self.line("Moldova emergency services", "Служби Молдови", "112")
        self.assertEqual(v.check_line("MOLDOVA · TRANSNISTRIA", ln)[0], "confirmed")
        PAGES["https://112.md/"] = "SERVICIUL NAȚIONAL UNIC PENTRU APELURILE DE URGENȚĂ 112 AL REPUBLICII MOLDOVA 999 este numărul unic pentru apelurile de urgență"
        self.assertEqual(v.check_line("MOLDOVA · TRANSNISTRIA", ln)[0], "unverified")
        PAGES["https://112.md/"] = "HTTP 403"; PAGES.pop("https://112.md/")
        self.assertEqual(v.check_line("MOLDOVA · TRANSNISTRIA", ln)[0], "failed")

    def test_embassy_hotline_number_and_wrong_country(self):
        ln = self.line("Embassy hotline 24/7 (no consular questions)", "Гаряча лінія посольства", "+373 69 64 99 76")
        u = "https://moldova.mfa.gov.ua/"
        PAGES[u] = 'Посольство України в Республіці Молдова "Гаряча лінія" 24/7 телефон: (+373) 69 64 99 76 (WhatsApp, Viber)'
        r = v.check_line("MOLDOVA · TRANSNISTRIA", ln); self.assertEqual(r[0], "confirmed")
        PAGES[u] = 'Посольство України в Республіці Молдова "Гаряча лінія" 24/7 телефон: (+373) 60 17 08 96'
        self.assertEqual(v.check_line("MOLDOVA · TRANSNISTRIA", ln)[0], "unverified")
        PAGES[u] = 'Посольство України в Польщі "Гаряча лінія" 24/7 телефон: (+373) 69 64 99 76'
        self.assertEqual(v.check_line("MOLDOVA · TRANSNISTRIA", ln)[0], "unverified")

    def test_number_not_matched_as_substring(self):
        u = "https://ombudsman.gov.ua/uk/news_details/41-ukrayinec-povernuvsya-z-okupaciyi-cherez-volin"
        PAGES[u] = "Уповноважений Верховної Ради України з прав людини тимчасово окупованих територій: +38 093 406 80 179"
        r = v.check_line("FROM OCCUPIED AREAS OR ABROAD", self.line("Ombudsman · messengers", "Омбудсман", "+380 93 406 80 17"))
        self.assertEqual(r[0], "unverified")

    def test_moldova_112_not_confirmed_by_national_rule(self):
        PAGES["https://mvs.gov.ua/mvs-specprojekti/sluzba-112-usi-ekstreni-sluzbi-za-jedinim-nomerom"] = "112 - це номер для звернення"
        r = v.check_line("MOLDOVA · TRANSNISTRIA", self.line("Moldova emergency services", "Служби Молдови", "112"))
        self.assertNotEqual(r[0], "confirmed")

    def test_mines_101(self):
        u = "https://bezpeka.dsns.gov.ua/materials/shcho-robyty-yakshcho-vyyavyly-minu-abo-inshyy-vybukhonebezpechnyy-predmet"
        PAGES[u] = "вибухонебезпечні предмети: не підходьте, не чіпайте, відійдіть назад тим самим шляхом. Телефонуйте 101, залишайтеся"
        r = v.check_line("MINES & AIR RAIDS", self.line("Back away the same way, then call", "Відійдіть тим самим шляхом, потім дзвоніть", "101"))
        self.assertEqual(r[0], "confirmed")

    def test_existing_six_rules_still_work(self):
        PAGES["https://1548.gov.ua/about/"] = "Адреса Гарячої лінії Телефони 1548"
        r = v.check_line("HOTLINES", self.line("Crisis & evacuation line 24/7", "Кризова лінія", "1548"))
        self.assertEqual(r[0], "confirmed")

if __name__ == "__main__": unittest.main()
