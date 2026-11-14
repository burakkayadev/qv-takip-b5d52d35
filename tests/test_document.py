"""Sipariş kurma, tekilleştirme, pencere, hatırlatma ve yazma mantığının birim testleri."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

import scraper

NOW = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)
DELIVERY = scraper.CALENDARS[0]
RETURN = next(spec for spec in scraper.CALENDARS if spec.key == "return")


def make_order(order_id: str, day: str, kind: str = "delivery", time: str = "", location: str = "Demo Otel",
               calendar: str = "Calendar Delivery") -> dict:
    return {"id": order_id, "type": kind, "date": day, "time": time, "cruise": False,
            "source_calendar": calendar, "title": location, "location_name": location,
            "waybill": {}, "dr_form": {}, "raw": {}}


def details_for(html: str) -> dict:
    return scraper.parse_detail_documents([html])


# ---- Pencere filtresi ------------------------------------------------------

@pytest.mark.parametrize("day, expected", [
    ("2026-11-24", False), ("2026-11-25", True), ("2026-12-04", True), ("2026-12-05", False),
    ("", False), ("25.11.2026", False),
])
def test_in_window_includes_today_plus_nine_days(today, day, expected):
    assert scraper.in_window(day, today, 10) is expected


def test_today_is_computed_in_istanbul_timezone():
    assert scraper.istanbul_today({"QV_TODAY": "2026-01-02"}) == date(2026, 1, 2)
    assert isinstance(scraper.istanbul_today({}), date)


# ---- Sipariş kurma ---------------------------------------------------------

def test_build_order_uses_first_line_of_delivery_or_return_location():
    details = details_for("""<table>
      <tr><th>Delivery Location</th><td>Pera Köşk Otel<br>Deneme Sok. No:12</td></tr>
      <tr><th>Return Location</th><td>Marmara Demo Airport Hotel<br>Havalimanı Yolu</td></tr>
      <tr><th>QV Booking ID</th><td>QV-700102</td></tr></table>""")
    delivery = scraper.build_order(DELIVERY, "2026-11-26", "09:30", "09:30 Nordwind Tours", details)
    returning = scraper.build_order(RETURN, "2026-12-03", "", "Nordwind iade", details)
    assert (delivery["id"], delivery["type"], delivery["title"]) == ("QV-700102", "delivery", "Nordwind Tours")
    assert delivery["location_name"] == "Pera Köşk Otel"
    assert returning["location_name"] == "Marmara Demo Airport Hotel"
    assert returning["source_calendar"] == "Calendar Return" and returning["cruise"] is False


def test_build_order_without_details_falls_back_to_title_and_stable_id():
    first = scraper.build_order(DELIVERY, "2026-11-26", "", "12 Apostel Demo Otel", None)
    second = scraper.build_order(DELIVERY, "2026-11-26", "", "12 Apostel Demo Otel", None)
    assert first["location_name"] == first["title"] == "12 Apostel Demo Otel"
    assert first["id"] == second["id"] and first["id"].startswith("delivery-2026-11-26-")
    assert first["dr_form"]["equipment"] == [] and first["raw"] == {}


def test_build_order_takes_date_from_modal_when_calendar_date_unknown():
    details = details_for("<p>Tour Start: 26.11.2026</p><p>Tour End: 02.12.2026</p>")
    assert scraper.build_order(DELIVERY, "", "", "x", details)["date"] == "2026-11-26"
    assert scraper.build_order(RETURN, "", "", "x", details)["date"] == "2026-12-02"
    assert scraper.build_order(DELIVERY, "", "", "x", None) is None


# ---- Tekilleştirme ve sıralama ---------------------------------------------

def test_dedupe_is_per_list_and_prefers_richer_record():
    poor = make_order("QV-1", "2026-11-26")
    rich = {**make_order("QV-1", "2026-11-26"), "waybill": {"client": "Aurora"}}
    other = make_order("QV-2", "2026-11-26")
    assert scraper.dedupe_orders([poor, rich, other]) == [rich, other]


def test_same_booking_may_appear_in_both_deliveries_and_returns(settings):
    results = {"delivery": [make_order("QV-1", "2026-11-25"), make_order("QV-1", "2026-11-25")],
               "cruise_delivery": [], "cruise_return": [],
               "return": [make_order("QV-1", "2026-11-30", "return", calendar="Calendar Return")]}
    document = scraper.assemble_document(None, results, 0, settings, NOW)
    assert [o["id"] for o in document["deliveries"]] == ["QV-1"]
    assert [o["id"] for o in document["returns"]] == ["QV-1"]
    assert (document["status"], document["status_message"], document["demo"]) == ("ok", "", False)


def test_orders_are_sorted_by_date_time_id():
    orders = [make_order("B", "2026-11-26", time="09:00"), make_order("A", "2026-11-26", time="09:00"),
              make_order("C", "2026-11-25", time="18:00"), make_order("D", "2026-11-26")]
    assert [o["id"] for o in scraper.sort_orders(orders)] == ["C", "D", "A", "B"]


# ---- Kısmi başarısızlık ----------------------------------------------------

def test_failed_calendar_keeps_previous_orders_and_reports_partial(settings):
    previous = {"demo": False, "deliveries": [
        make_order("OLD-IN", "2026-11-27", calendar="Cruises Calendar Delivery"),
        make_order("OLD-PAST", "2026-11-20", calendar="Cruises Calendar Delivery"),
        make_order("OLD-OTHER", "2026-11-27", calendar="Calendar Delivery"),
    ], "returns": []}
    results = {"delivery": [make_order("NEW", "2026-11-25")], "cruise_delivery": None,
               "return": [], "cruise_return": []}
    document = scraper.assemble_document(previous, results, 0, settings, NOW)
    assert [o["id"] for o in document["deliveries"]] == ["NEW", "OLD-IN"]
    assert document["status"] == "partial"
    assert "Cruises Calendar Delivery" in document["status_message"]


def test_demo_records_are_never_preserved(settings):
    previous = {"demo": True, "deliveries": [make_order("DEMO", "2026-11-27")], "returns": []}
    results = {"delivery": None, "cruise_delivery": [], "return": [], "cruise_return": []}
    assert scraper.assemble_document(previous, results, 0, settings, NOW)["deliveries"] == []


def test_login_failure_document_keeps_orders_and_only_changes_status(settings):
    previous = {**scraper.empty_document(settings, NOW - timedelta(hours=5)),
                "deliveries": [make_order("QV-1", "2026-11-25")]}
    document = scraper.status_only_document(previous, "login_failed", "mesaj", settings, NOW)
    assert document["deliveries"] == previous["deliveries"]
    assert document["generated_at"] == previous["generated_at"]
    assert (document["status"], document["status_message"]) == ("login_failed", "mesaj")
    assert previous["status"] == "ok"  # girdi değiştirilmez


# ---- "Değişmediyse yazma" ---------------------------------------------------

def test_should_write_skips_unchanged_fresh_file(settings):
    document = {**scraper.empty_document(settings, NOW), "deliveries": [make_order("QV-1", "2026-11-25")]}
    fresh = {**document, "generated_at": scraper.utc_stamp(NOW - timedelta(minutes=90))}
    stale = {**document, "generated_at": scraper.utc_stamp(NOW - timedelta(minutes=121))}
    assert scraper.should_write(fresh, document, NOW) is False
    assert scraper.should_write(stale, document, NOW) is True
    assert scraper.should_write(None, document, NOW) is True
    assert scraper.should_write({**fresh, "status": "partial"}, document, NOW) is True
    assert scraper.should_write({**fresh, "deliveries": []}, document, NOW) is True
    assert scraper.should_write({**fresh, "generated_at": "bozuk"}, document, NOW) is True


def test_write_if_changed_writes_utf8_and_then_leaves_file_alone(settings):
    document = {**scraper.empty_document(settings, NOW), "deliveries": [make_order("QV-1", "2026-11-25", location="Köşk")]}
    assert scraper.write_if_changed(settings, None, document, NOW) is True
    text = settings.output.read_text(encoding="utf-8")
    assert '"location_name": "Köşk"' in text and text.endswith("}\n") and '\n  "demo": false' in text
    saved = json.loads(text)
    assert saved["generated_at"] == "2026-11-25T09:00:00Z"
    later = NOW + timedelta(minutes=30)
    assert scraper.write_if_changed(settings, scraper.load_previous(settings.output), document, later) is False
    assert settings.output.read_text(encoding="utf-8") == text
    assert list(settings.output.parent.glob("*.tmp")) == []


def test_load_previous_tolerates_missing_and_corrupt_files(settings):
    assert scraper.load_previous(settings.output) is None
    settings.output.write_text("{bozuk", encoding="utf-8")
    assert scraper.load_previous(settings.output) is None


# ---- Hatırlatma ve bildirim ------------------------------------------------

def test_reminder_text_lists_tomorrows_locations():
    tomorrow = date(2026, 11, 26)
    deliveries = [make_order("1", "2026-11-26", location="Conrad"), make_order("2", "2026-11-26", location="Four Seasons"),
                  make_order("3", "2026-11-26", location="Conrad"), make_order("4", "2026-11-27", location="Başka")]
    returns = [make_order("5", "2026-11-26", "return", location="Hilton"), make_order("6", "2026-11-26", "return", location="Pera")]
    text = scraper.build_reminder_text(deliveries, returns, tomorrow)
    assert text == "📦 Yarın 3 Teslimat (Conrad, Four Seasons) ve 🔄 2 İade Alımı (Hilton, Pera) var."


def test_reminder_text_handles_empty_days_and_truncates_names():
    tomorrow = date(2026, 11, 26)
    many = [make_order(str(i), "2026-11-26", location=f"Otel {i}") for i in range(6)]
    assert scraper.build_reminder_text([], [], tomorrow) == "Yarın teslimat veya iade yok."
    assert scraper.build_reminder_text(many, [], tomorrow) == "📦 Yarın 6 Teslimat (Otel 0, Otel 1, Otel 2, Otel 3, …) var."
    assert scraper.build_reminder_text([], many[:1], tomorrow) == "🔄 Yarın 1 İade Alımı (Otel 0) var."


def test_send_ntfy_posts_utf8_body_with_ascii_headers(settings, monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return b""

    def fake_urlopen(request, timeout):
        captured["request"] = request
        return FakeResponse()

    monkeypatch.setattr(scraper.urllib.request, "urlopen", fake_urlopen)
    with_topic = scraper.load_settings({"NTFY_TOPIC": "kurgusal-konu", "NTFY_URL": "http://127.0.0.1:9/ntfy"})
    assert scraper.send_ntfy(with_topic, scraper.LOGIN_FAILED_MESSAGE) is True
    request = captured["request"]
    assert request.full_url == "http://127.0.0.1:9/ntfy/kurgusal-konu" and request.get_method() == "POST"
    assert request.data.decode("utf-8") == "⚠️ Quietvox oturumu açılamadı, şifreyi kontrol edin"
    assert all(value.isascii() for value in request.headers.values())
    assert scraper.send_ntfy(settings, "konu yok") is False


def test_send_ntfy_failure_does_not_raise(monkeypatch):
    def boom(request, timeout):
        raise OSError("ağ yok")

    monkeypatch.setattr(scraper.urllib.request, "urlopen", boom)
    assert scraper.send_ntfy(scraper.load_settings({"NTFY_TOPIC": "kurgusal-konu"}), "x") is False


def test_reminder_requires_topic_and_flag(monkeypatch):
    sent = []
    monkeypatch.setattr(scraper, "send_ntfy", lambda settings, message, **kw: sent.append(message) or True)
    document = {"deliveries": [make_order("1", "2026-11-26", location="Conrad")], "returns": []}
    base = {"QV_TODAY": "2026-11-25", "NTFY_TOPIC": "kurgusal-konu"}
    assert scraper.maybe_send_reminder(scraper.load_settings(base), document) is False
    assert scraper.maybe_send_reminder(scraper.load_settings({**base, "SEND_REMINDER": "1", "NTFY_TOPIC": ""}), document) is False
    assert scraper.maybe_send_reminder(scraper.load_settings({**base, "SEND_REMINDER": "1"}), document) is True
    assert sent == ["📦 Yarın 1 Teslimat (Conrad) var."]


def test_secret_filter_masks_passwords_in_logs():
    import logging

    record = logging.LogRecord("q", logging.INFO, "", 0, "fill(%s) başarısız", ("test-sifre-123",), None)
    scraper.SecretFilter(["test-sifre-123"]).filter(record)
    assert record.getMessage() == "fill(***) başarısız"
