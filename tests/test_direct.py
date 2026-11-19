"""Gerçek Quietvox uçlarının (feed + WayBill + D&R) ayrıştırıcıları için birim testleri.

HTML iskeletleri canlı sitede gözlenen yapıyı taklit eder; içerik tamamen kurgusaldır.
"""
from datetime import date

import scraper

TODAY = date(2026, 11, 14)

WAYBILL_HTML = """
<div id="waybill-wrapper">
  <table id="tour-guide" class="waybill-form-table">
    <tr><th>Tour manager</th></tr><tr><td>Ayşe Örnek +90 555 000 00 01</td></tr>
  </table>
  <table id="tour-reference" class="waybill-form-table">
    <tr><th>Tour reference</th><th>Qv id</th></tr>
    <tr><td>DEMO-TR-001</td><td>2000000001</td></tr>
  </table>
  <table id="client" class="waybill-form-table">
    <tr><th>Client</th></tr><tr><td>Örnek Travel GmbH</td></tr>
  </table>
  <table id="delivery-location" class="waybill-form-table">
    <tr><th>Delivery location</th><th>Return location</th></tr><tr><td></td><td></td></tr>
  </table>
  <table id="tour" class="waybill-form-table">
    <tr><th>Tour start</th><th>Tour end</th></tr><tr><td>14.11.2026</td><td>18.11.2026</td></tr>
  </table>
</div><script>var x = 1;</script>
"""

DR_HTML = """
<div id="delivery-return-wrapper">
  <table id="client" class="delivery-return-form-table client-tour">
    <tr><th>Customer</th><td colspan="3">Örnek Travel GmbH</td></tr>
    <tr><th>Reference</th><td colspan="3">DEMO-TR-001</td></tr>
    <tr><th>QV Booking ID</th><td colspan="3">2000000001</td></tr>
    <tr><th>Clients</th><td>32</td><th>Groups</th><td>1</td></tr>
    <tr><td colspan="4"></td></tr>
    <tr><th>Tour Manager</th><td>Ayşe Örnek</td><th>Instruction Language</th><td>German</td></tr>
    <tr><th>Cell Phone</th><td>+905550000001</td></tr>
  </table>
  <table class="delivery-return-form-table client-tour-extend">
    <tr><th>Service Start Date</th><td>14.11.2026</td>
        <th>Delivery Location</th><td>Turkey (TR)<br>Demo Otel Sultanahmet<br>Örnek Cad. No:1<br>34122 Fatih<br>+90 212 000 00 01</td>
        <td><svg></svg></td></tr>
    <tr><th>Delivery Station</th><td>Istanbul</td></tr>
  </table>
  <table id="delivered-retruned" class="delivery-return-form-table big-table">
    <tr><th>Equipment</th><th>Delivered by quietvox</th><th>Received by Customer</th>
        <th>Returned by Customer</th><th>Received by quietvox</th></tr>
    <tr><td># Bag ID</td><td></td>
        <td><table class="bag-ids"><tr><td>Yes</td><td></td><td>No</td><td></td></tr></table></td>
        <td><table class="bag-ids"><tr><td>Yes</td><td></td><td>No</td><td></td></tr></table></td>
        <td></td></tr>
    <tr><td># Clients</td><td>32</td><td></td><td></td><td></td></tr>
    <tr><td># QV9 TX EU(QV9.2)</td><td>2</td><td></td><td>2</td><td></td></tr>
    <tr><td># QV9 RX EU(QV9.1)</td><td>32</td><td></td><td>32</td><td></td></tr>
    <tr><td># Battery AA disposable(QV1.38)</td><td>68</td><td></td><td></td><td></td></tr>
    <tr><td>Weight:</td><td>2.75 Kg / 6.06 Pounds</td><td></td><td>2.38 Kg / 5.25 Pounds</td><td></td></tr>
  </table>
  <table class="delivery-return-form-table client-tour-extend">
    <tr><th>Service End Date</th><td>18.11.2026</td>
        <th>Return Location</th><td>Demo Bosphorus Hotel<br>Sahil Yolu No:12</td><td><svg></svg></td></tr>
    <tr><th>Return Station</th><td>Istanbul</td></tr>
  </table>
</div>
"""


def test_feed_url_uses_sibling_endpoint_and_kind():
    url = scraper.feed_url_for(
        "https://example.test/seasons/calendar/returnCalendar.html?x=1#a", "return", TODAY, 10)

    assert url.startswith("https://example.test/seasons/calendar/getContent.html?type=return&start=")
    assert "&end=" in url


def test_feed_items_keep_only_window_and_split_title_lines():
    payload = [
        {"id": 2000000001, "title": "Örnek Travel<br>Demo Otel<br>", "start": "2026-11-14", "allDay": True,
         "status": 2},
        {"id": 2000000002, "title": "Geçmiş", "start": "2026-11-13", "allDay": True},
        {"id": 2000000003, "title": "Uzak", "start": "2026-11-24", "allDay": True},
        {"title": "Kimliksiz", "start": "2026-11-15"},
    ]

    items = scraper.parse_feed_items(payload, TODAY, 10)

    assert items == [{"id": "2000000001", "date": "2026-11-14", "time": "", "title": "Örnek Travel · Demo Otel",
                      "status": "delivered"}]


def test_feed_items_reject_unknown_shape_but_accept_empty_list():
    assert scraper.parse_feed_items({"error": "x"}, TODAY, 10) is None
    assert scraper.parse_feed_items([], TODAY, 10) == []


def test_direct_documents_fill_waybill_and_dr_form():
    details = scraper.parse_direct_documents(DR_HTML, WAYBILL_HTML)
    waybill, form = details["waybill"], details["dr_form"]

    assert waybill["tour_manager_name"] == "Ayşe Örnek"
    assert waybill["tour_manager_phone"] == "+905550000001"
    assert waybill["tour_reference"] == "DEMO-TR-001"
    assert waybill["qv_id"] == "2000000001"
    assert waybill["client"] == "Örnek Travel GmbH"
    assert waybill["delivery_location"].split("\n")[0] == "Demo Otel Sultanahmet"
    assert waybill["delivery_location"].split("\n")[-1] == "Turkey (TR)"
    assert waybill["delivery_phone"] == "+90 212 000 00 01"
    assert waybill["return_location"].split("\n")[0] == "Demo Bosphorus Hotel"
    assert (waybill["tour_start"], waybill["tour_end"]) == ("2026-11-14", "2026-11-18")
    assert form["customer"] == "Örnek Travel GmbH"
    assert form["qv_booking_id"] == form["qr_content"] == "2000000001"
    assert (form["clients"], form["groups"]) == ("32", "1")
    assert form["instruction_language"] == "German"
    assert form["service_start_date"] == "2026-11-14"
    assert form["delivery_station"] == "Istanbul"


def test_direct_equipment_skips_non_items_and_reads_weights():
    details = scraper.parse_direct_documents(DR_HTML, WAYBILL_HTML)
    form = details["dr_form"]

    assert [row["item"] for row in form["equipment"]] == [
        "QV9 TX EU(QV9.2)", "QV9 RX EU(QV9.1)", "Battery AA disposable(QV1.38)"]
    assert form["equipment"][0] == {
        "item": "QV9 TX EU(QV9.2)", "quantity": "2", "weight": "", "return_quantity": "2"}
    assert form["equipment"][2]["return_quantity"] == ""
    assert form["total_weight"] == "2.75 Kg / 6.06 Pounds"
    assert details["raw"]["Return weight"] == "2.38 Kg / 5.25 Pounds"


def test_login_page_instead_of_documents_counts_as_unreadable():
    login_html = "<form><input type='text'><input type='password'></form>"

    details = scraper.parse_direct_documents(login_html, login_html)

    assert scraper.has_direct_content(details) is False
    assert scraper.has_direct_content(None) is False


def test_same_order_on_two_days_stays_as_two_records():
    orders = [
        {"id": "7", "date": "2026-11-15", "time": ""},
        {"id": "7", "date": "2026-11-14", "time": ""},
        {"id": "8", "date": "2026-11-14", "time": ""},
    ]

    result = scraper.separate_repeated_ids(orders)

    assert [order["id"] for order in result] == ["7", "8", "7_2026-11-15"]


def test_feed_items_can_look_back_for_history():
    payload = [{"id": 5, "title": "Eski", "start": "2026-11-01", "status": 6}]

    assert scraper.parse_feed_items(payload, TODAY, 10) == []
    assert scraper.parse_feed_items(payload, TODAY, 10, past=31)[0]["status"] == "returned"


def test_old_order_is_reused_from_cache_with_fresh_status():
    cached = {"id": "5", "date": "2026-11-01", "status": "delivered", "title": "Eski",
              "waybill": {"client": "Örnek"}, "dr_form": {}}
    item = {"id": "5", "date": "2026-11-01", "time": "", "title": "Eski", "status": "returned"}

    reused = scraper.reusable_order(item, cached, TODAY)

    assert reused["status"] == "returned" and reused["waybill"] == {"client": "Örnek"}


def test_near_or_unread_orders_are_never_reused():
    filled = {"id": "5", "date": "2026-11-15", "waybill": {"client": "Örnek"}, "dr_form": {}}
    empty = {"id": "6", "date": "2026-11-01", "waybill": {"client": ""}, "dr_form": {}}

    assert scraper.reusable_order({"id": "5", "date": "2026-11-15", "title": "", "status": ""}, filled, TODAY) is None
    assert scraper.reusable_order({"id": "6", "date": "2026-11-01", "title": "", "status": ""}, empty, TODAY) is None
    assert scraper.reusable_order({"id": "7", "date": "2026-11-01", "title": "", "status": ""}, None, TODAY) is None


def _order(day, status, name="Demo Otel"):
    return {"date": day, "status": status, "location_name": name}


def test_today_reminder_lists_only_pending_work():
    deliveries = [_order("2026-11-14", "confirmed"), _order("2026-11-14", "delivered"),
                  _order("2026-11-14", "cancelled")]
    returns = [_order("2026-11-14", "delivered", "Demo Palace"), _order("2026-11-14", "returned")]

    text = scraper.build_today_text(deliveries, returns, TODAY)

    assert text == ("📦 Bugün 1 Teslimat (Demo Otel) bekliyor.\n"
                    "🔄 Bugün 1 İade Alımı (Demo Palace) bekliyor.")
    assert scraper.build_today_text([_order("2026-11-14", "delivered")], [], TODAY) == ""


def test_missed_return_warning_covers_past_week_only():
    returns = [_order("2026-11-13", "delivered", "Dün Otel"), _order("2026-11-13", "returned"),
               _order("2026-11-14", "delivered"), _order("2026-11-01", "delivered")]

    assert scraper.build_missed_text(returns, TODAY) == "⚠️ Almayı unuttun: 1 iade hâlâ alınmadı (Dün Otel)."
    assert scraper.build_missed_text([], TODAY) == ""


def test_reminder_kinds_choose_their_messages():
    document = {"deliveries": [_order("2026-11-14", "confirmed")], "returns": [_order("2026-11-13", "delivered")]}

    assert len(scraper.reminder_messages("morning", document, TODAY)) == 2
    assert scraper.reminder_messages("evening", document, TODAY) == ["Yarın teslimat veya iade yok."]
    assert scraper.reminder_messages("", document, TODAY) == []


def test_feed_status_is_the_raw_portal_status_and_never_guessed():
    # Canlı denetimde (19.11.2026) yeşil renk / geçmiş tarih tahmini 50 Delivered siparişi
    # "returned" gösteriyordu. Durum yalnızca portalın ham kodundan gelir.
    payload = [
        {"id": 101, "title": "Yeşil Hex", "start": "2026-11-14", "status": 2, "backgroundColor": "#28a745"},
        {"id": 102, "title": "Yeşil Sınıf", "start": "2026-11-14", "status": 2, "className": "event-green returned"},
        {"id": 103, "title": "Geçmiş İade", "start": "2026-11-13", "status": 2},
        {"id": 104, "title": "Gerçekten Alınmış", "start": "2026-11-13", "status": 6},
        {"id": 105, "title": "Kodsuz", "start": "2026-11-13"},
    ]

    items = scraper.parse_feed_items(payload, TODAY, 10, past=7)

    assert [item["status"] for item in items] == ["delivered", "delivered", "delivered", "returned", ""]


def test_resolve_order_status_maps_codes_without_heuristics():
    assert [scraper.resolve_order_status(code) for code in (1, 2, 3, 4, 5, 6, 8, "6", "Returned", None, 7)] == [
        "reserved", "delivered", "lost", "cancelled", "confirmed", "returned", "duplicated",
        "returned", "returned", "", "7",
    ]


def test_due_reminder_picks_each_slot_once_within_its_window():
    from datetime import datetime

    def at(hour):
        return datetime(2026, 11, 14, hour, 30)

    assert scraper.due_reminder(at(7), []) == ""
    assert scraper.due_reminder(at(8), []) == "morning"
    assert scraper.due_reminder(at(10), []) == "morning"
    assert scraper.due_reminder(at(10), ["morning"]) == ""  # bugün gönderildi: tekrar yok
    assert scraper.due_reminder(at(11), []) == ""  # pencere (3 saat) kapandı
    assert scraper.due_reminder(at(14), ["morning"]) == "noon"
    assert scraper.due_reminder(at(20), ["morning", "noon"]) == "evening"
    assert scraper.due_reminder(at(21), []) == ""


def test_sent_reminders_only_counts_today():
    previous = {"reminders_sent": {"date": "2026-11-14", "kinds": ["morning", 5]}}
    assert scraper.sent_reminders(previous, TODAY) == ["morning"]
    assert scraper.sent_reminders({"reminders_sent": {"date": "2026-11-13", "kinds": ["noon"]}}, TODAY) == []
    assert scraper.sent_reminders(None, TODAY) == []


def test_explicit_reminder_is_sent_but_not_recorded(monkeypatch):
    from types import SimpleNamespace

    sent = []
    monkeypatch.setattr(scraper, "maybe_send_reminder", lambda settings, document: sent.append(settings.reminder) or True)
    monkeypatch.setattr(scraper, "replace", lambda settings, **changes: SimpleNamespace(**{**vars(settings), **changes}))
    settings = SimpleNamespace(reminder="evening", today=TODAY)
    document = {"deliveries": [], "returns": []}

    assert scraper.send_due_reminders(settings, None, document) == document  # elle deneme günü kilitlemez
    assert sent == ["evening"]


def test_missed_return_lists_every_return_still_delivered_on_the_portal():
    # Uygulama tarafındaki işaret ya da D&R'deki iade adedi portal durumu değildir: hâlâ Delivered
    # olan her iade "almayı unuttun" uyarısına girer; yalnızca portalda Returned olan çıkar.
    flagged = {**_order("2026-11-13", "delivered", "Yeşil Otel"), "qv_done": True}
    with_qty = {
        **_order("2026-11-13", "delivered", "Tartılı Otel"),
        "dr_form": {"equipment": [{"item": "QV9 RX", "quantity": "20", "return_quantity": "20"}]},
    }
    pending = _order("2026-11-13", "delivered", "Gerçekten Bekleyen")
    returned = _order("2026-11-13", "returned", "Alınmış Otel")

    assert scraper.build_missed_text([flagged, with_qty, pending, returned], TODAY) == (
        "⚠️ Almayı unuttun: 3 iade hâlâ alınmadı (Yeşil Otel, Tartılı Otel, Gerçekten Bekleyen)."
    )

