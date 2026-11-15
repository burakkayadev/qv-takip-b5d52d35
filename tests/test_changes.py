"""Sipariş değişikliği uyarısı ve bildirim bağlantıları için testler (kurgusal veri)."""
import copy
from datetime import date, datetime, timezone

import scraper

TODAY = date(2026, 11, 15)
NOW = datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)


def _order(order_id="2000000001", day="2026-11-16", pax="20", rx="20", status="confirmed"):
    return {
        "id": order_id, "date": day, "status": status, "location_name": "Demo Otel",
        "waybill": {"tour_manager_phone": "+905550000001"},
        "dr_form": {"customer": "Örnek Travel", "clients": pax,
                    "equipment": [{"item": "QV9 RX EU(QV9.1)", "quantity": rx},
                                  {"item": "Battery AA disposable(QV1.38)", "quantity": "40"}]},
    }


def _doc(*orders, returns=()):
    return {"demo": False, "deliveries": list(orders), "returns": list(returns)}


def test_pax_and_equipment_change_is_reported():
    previous = _doc(_order())
    current = _doc(_order(pax="22", rx="22"))

    updated, lines = scraper.annotate_changes(previous, current, TODAY, NOW)

    assert lines == ["Demo Otel (2000000001, 16.11): kişi 20→22; QV9 RX EU 20→22"]
    assert updated["deliveries"][0]["changes"] == [{"at": "2026-11-15T09:00:00Z",
                                                   "text": "kişi 20→22; QV9 RX EU 20→22"}]
    assert "changes" not in current["deliveries"][0]  # girdi belge değiştirilmez


def test_cancel_and_date_change():
    previous = _doc(_order(), _order("2000000002"))
    current = _doc(_order(status="cancelled"), _order("2000000002", day="2026-11-18"))

    _updated, lines = scraper.annotate_changes(previous, current, TODAY, NOW)

    assert lines[0].endswith("İPTAL EDİLDİ")
    assert lines[1].endswith("tarih 16.11→18.11")


def test_no_alarm_for_unchanged_far_unreadable_or_demo_orders():
    unchanged = _doc(_order())
    far_prev, far_now = _doc(_order(day="2026-12-20")), _doc(_order(day="2026-12-20", pax="30"))
    unreadable = _order(pax="30")
    unreadable["dr_form"] = {"customer": "", "clients": "", "equipment": []}

    assert scraper.annotate_changes(unchanged, copy.deepcopy(unchanged), TODAY, NOW)[1] == []
    assert scraper.annotate_changes(far_prev, far_now, TODAY, NOW)[1] == []
    assert scraper.annotate_changes(_doc(_order()), _doc(unreadable), TODAY, NOW)[1] == []
    assert scraper.annotate_changes({**_doc(_order()), "demo": True}, _doc(_order(pax="30")), TODAY, NOW)[1] == []
    assert scraper.annotate_changes(None, _doc(_order()), TODAY, NOW)[1] == []


def test_old_change_notes_expire_after_three_days():
    old = _order()
    old["changes"] = [{"at": "2026-11-15T08:00:00Z", "text": "kişi 18→20"},
                      {"at": "2026-11-10T08:00:00Z", "text": "eski"}]

    updated, lines = scraper.annotate_changes(_doc(old), _doc(_order()), TODAY, NOW)

    assert lines == []
    assert updated["deliveries"][0]["changes"] == [{"at": "2026-11-15T08:00:00Z", "text": "kişi 18→20"}]


def test_change_message_is_capped():
    text = scraper.change_message([f"Otel {i}: kişi 1→2" for i in range(9)])

    assert text.startswith("🔁 Sipariş değişti")
    assert text.count("• ") == scraper.MAX_CHANGE_LINES
    assert text.endswith("…ve 3 sipariş daha")


def test_reminder_click_targets():
    assert scraper.reminder_click("morning", "⚠️ Almayı unuttun: 1 iade") == "?tab=returns&filter=overdue"
    assert scraper.reminder_click("evening", "📦 Yarın 2 Teslimat var.") == "?filter=tomorrow"
    assert scraper.reminder_click("noon", "🔄 Bugün 1 İade Alımı bekliyor.") == "?tab=returns&filter=today"
    assert scraper.reminder_click("morning", "📦 Bugün 3 Teslimat bekliyor.") == "?tab=deliveries&filter=today"


def test_ntfy_click_header_uses_app_url(monkeypatch):
    sent = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b""

    def fake_urlopen(request, timeout):
        sent.update(dict(request.header_items()))
        return Response()

    monkeypatch.setattr(scraper.urllib.request, "urlopen", fake_urlopen)
    settings = scraper.load_settings({"QV_USER": "u", "QV_PASS": "p", "NTFY_TOPIC": "t",
                                      "APP_URL": "https://demo.github.io/repo/"})

    assert scraper.send_ntfy(settings, "deneme", click="?filter=today") is True
    assert sent.get("Click") == "https://demo.github.io/repo/?filter=today"
