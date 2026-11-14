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
        {"id": 2000000001, "title": "Örnek Travel<br>Demo Otel<br>", "start": "2026-11-14", "allDay": True},
        {"id": 2000000002, "title": "Geçmiş", "start": "2026-11-13", "allDay": True},
        {"id": 2000000003, "title": "Uzak", "start": "2026-11-24", "allDay": True},
        {"title": "Kimliksiz", "start": "2026-11-15"},
    ]

    items = scraper.parse_feed_items(payload, TODAY, 10)

    assert items == [{"id": "2000000001", "date": "2026-11-14", "time": "", "title": "Örnek Travel · Demo Otel"}]


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
