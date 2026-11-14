"""Saf ayrıştırma fonksiyonlarının birim testleri (Playwright gerekmez; veriler kurgusaldır)."""
from __future__ import annotations

import pytest

import scraper

WAYBILL_TABLE = """
<div class="modal-body"><table>
  <tr><th>Tour Manager</th><td>Elif Deneme (+90 555 010 00 01)</td></tr>
  <tr><th>Tour  Reference :</th><td>AUR-2611</td></tr>
  <tr><th>QV ID</th><td>QV-700101</td></tr>
  <tr><th>CLIENT</th><td>Aurora Reisen GmbH</td></tr>
  <tr><th>Delivery Location</th><td>Hotel Lale Sarayı<br>Kurgu Cad. No:7<br>34122 Fatih / İstanbul<br>Tel: +90 212 010 00 01</td></tr>
  <tr><th>Return Location</th><td>Haliç Demo Residence<br>İskele Sok. No:4</td></tr>
  <tr><th>Tour Start</th><td>25.11.2026</td></tr>
  <tr><th>Tour End</th><td>1 Dec 2026</td></tr>
  <tr><th>Internal Note</th><td>kurgusal not</td></tr>
</table></div>
"""

DR_FORM = """
<table>
  <tr><td>Customer:</td><td>Aurora Reisen GmbH</td><td>Reference:</td><td>AUR-2611</td></tr>
  <tr><td>QV Booking ID:</td><td>QV-700101</td><td>Clients:</td><td>30</td></tr>
  <tr><td>Groups:</td><td>1</td><td>Instruction Language:</td><td>German</td></tr>
  <tr><td>Service Start Date:</td><td>25/11/2026</td><td>Delivery Station:</td><td>Istanbul</td></tr>
</table>
<table>
  <thead><tr><th>Article</th><th>Qty</th><th>Weight (kg)</th></tr></thead>
  <tbody>
    <tr><td>QV9 TX EU</td><td>2</td><td>0.20 kg</td></tr>
    <tr><td>QV9 RX EU</td><td>30</td><td>2.10 kg</td></tr>
    <tr><td>Carrying case</td><td>1</td><td>1.80 kg</td></tr>
  </tbody>
  <tfoot><tr><td colspan="2">Total</td><td>4.10 kg</td></tr></tfoot>
</table>
"""


def first_wins(pairs):
    """Kazıyıcıdaki gibi: aynı etiket için ilk bulunan değer geçerlidir."""
    return dict(reversed(pairs))


# ---- Etiket çıkarımı -------------------------------------------------------

def test_extract_pairs_reads_th_td_rows():
    pairs = first_wins(scraper.extract_pairs(WAYBILL_TABLE))
    assert pairs["QV ID"] == "QV-700101"
    assert pairs["Return Location"] == "Haliç Demo Residence\nİskele Sok. No:4"


def test_extract_pairs_reads_two_and_four_column_td_rows():
    pairs = first_wins(scraper.extract_pairs(DR_FORM))
    assert pairs["Customer:"] == "Aurora Reisen GmbH"
    assert pairs["Delivery Station:"] == "Istanbul"


def test_extract_pairs_reads_definition_lists():
    html = "<dl><dt>Client</dt><dd>Nordwind Tours Ltd.</dd><dt>QV ID</dt><dd>QV-700102</dd></dl>"
    assert ("Client", "Nordwind Tours Ltd.") in scraper.extract_pairs(html)
    assert ("QV ID", "QV-700102") in scraper.extract_pairs(html)


def test_extract_pairs_reads_label_with_input_and_span():
    html = """
      <label for="c">Customer</label><input id="c" type="text" value="Sample Voyages SA">
      <label>Groups <input type="text" value="3"></label>
      <label>Delivery Station</label><span>Istanbul</span>
      <label for="lang">Instruction Language</label>
      <select id="lang"><option>English</option><option selected>German</option></select>
    """
    pairs = first_wins(scraper.extract_pairs(html))
    assert pairs["Customer"] == "Sample Voyages SA"
    assert pairs["Groups"] == "3"
    assert pairs["Delivery Station"] == "Istanbul"
    assert pairs["Instruction Language"] == "German"


def test_extract_pairs_reads_plain_label_colon_lines_with_multiline_address():
    html = """<div>Client: Demo Cruises Inc.</div>
      <div>Delivery Location: Galata Demo Terminal<br>Rıhtım Cad. No:1</div>
      <div>Return Location:</div><div>Karaköy Demo Rıhtımı<br>Rıhtım Cad. No:9</div>
      <div>Tour Start: 2026-11-26</div>"""
    pairs = first_wins(scraper.extract_pairs(html))
    assert pairs["Client"] == "Demo Cruises Inc."
    assert pairs["Delivery Location"] == "Galata Demo Terminal\nRıhtım Cad. No:1"
    assert pairs["Return Location"] == "Karaköy Demo Rıhtımı\nRıhtım Cad. No:9"
    assert pairs["Tour Start"] == "2026-11-26"


@pytest.mark.parametrize("label", ["QV Booking ID", "qv booking id:", "  QV   BOOKING-ID : ", "Qv_Booking_Id"])
def test_label_synonyms_tolerate_case_colon_and_spacing(label):
    assert scraper.LABEL_MAP[scraper.norm_label(label)] == ("dr_form", "qv_booking_id")


def test_client_and_clients_map_to_different_fields():
    details = scraper.map_pairs([("Client", "Aurora Reisen GmbH"), ("Clients", "30")])
    assert details["waybill"]["client"] == "Aurora Reisen GmbH"
    assert details["dr_form"]["clients"] == "30"


def test_unknown_labels_go_to_raw():
    details = scraper.parse_detail_documents([WAYBILL_TABLE])
    assert details["raw"] == {"Internal Note": "kurgusal not"}


def test_generic_phone_label_follows_previous_field():
    details = scraper.map_pairs([
        ("Tour Manager", "Deniz Örnek"), ("Phone", "+90 555 010 00 21"),
        ("Delivery Location", "Galata Demo Terminal"), ("Phone", "+90 212 010 00 21"),
    ])
    assert details["waybill"]["tour_manager_phone"] == "+90 555 010 00 21"
    assert details["waybill"]["delivery_phone"] == "+90 212 010 00 21"


# ---- Telefon ayırma --------------------------------------------------------

@pytest.mark.parametrize("text, rest, phone", [
    ("Elif Deneme (+90 555 010 00 01)", "Elif Deneme", "+90 555 010 00 01"),
    ("Kerem Sınama, Tel: 0555 010 00 02", "Kerem Sınama", "0555 010 00 02"),
    ("Kerem Sınama (0555 010 00 02)", "Kerem Sınama", "0555 010 00 02"),
    ("Kerem Sınama (0212) 010 00 02", "Kerem Sınama", "(0212) 010 00 02"),
    ("Zeynep Kurgu +90 555 010 00 09", "Zeynep Kurgu", "+90 555 010 00 09"),
    ("Deniz Örnek", "Deniz Örnek", ""),
    ("", "", ""),
])
def test_split_phone_separates_name_and_number(text, rest, phone):
    assert scraper.split_phone(text) == (rest, phone)


def test_split_phone_keeps_multiline_address_and_ignores_postcodes():
    address = "Hotel Lale Sarayı\nKurgu Cad. No:125\n34122 Fatih / İstanbul\n+90 212 010 00 01"
    assert scraper.split_phone(address) == (
        "Hotel Lale Sarayı\nKurgu Cad. No:125\n34122 Fatih / İstanbul", "+90 212 010 00 01")
    no_phone = "Boğaz Demo Hotel\nSahil Yolu No:21 34349 Beşiktaş"
    assert scraper.split_phone(no_phone) == (no_phone, "")


# ---- Tarih / saat ----------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("14.11.2026", "2026-11-14"), ("14/11/2026", "2026-11-14"), ("2026-11-14", "2026-11-14"),
    ("14 Nov 2026", "2026-11-14"), ("14. November 2026", "2026-11-14"), ("Nov 14, 2026", "2026-11-14"),
    ("3 Aralık 2026", "2026-12-03"), ("1.2.26", "2026-02-01"), ("2026-11-14T09:30:00", "2026-11-14"),
    ("Sat, 14 Nov 2026 09:30", "2026-11-14"), ("", ""), ("belli değil", "belli değil"),
    ("31.02.2026", "31.02.2026"),
])
def test_normalize_date(text, expected):
    assert scraper.normalize_date(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("09:30", "09:30"), ("9:30a", "09:30"), ("2p", "14:00"), ("12a", "00:00"), ("4:15 PM", "16:15"),
    ("14.11.2026 16:05", "16:05"), ("14.11.2026", ""), ("T09:30:00", "09:30"), ("", ""), ("25:99", ""),
])
def test_extract_time(text, expected):
    assert scraper.extract_time(text) == expected


# ---- Ekipman tablosu -------------------------------------------------------

def test_equipment_table_returns_all_rows_and_total():
    details = scraper.parse_detail_documents([DR_FORM])
    assert details["dr_form"]["equipment"] == [
        {"item": "QV9 TX EU", "quantity": "2", "weight": "0.20 kg"},
        {"item": "QV9 RX EU", "quantity": "30", "weight": "2.10 kg"},
        {"item": "Carrying case", "quantity": "1", "weight": "1.80 kg"},
    ]
    assert details["dr_form"]["total_weight"] == "4.10 kg"


def test_equipment_table_without_weight_column_and_without_header_tags():
    html = """<table><tr><td>Description</td><td>Amount</td></tr>
              <tr><td>Earphone</td><td>20</td></tr><tr><td>Microphone</td><td>2</td></tr></table>"""
    details = scraper.parse_detail_documents([html])
    assert details["dr_form"]["equipment"] == [
        {"item": "Earphone", "quantity": "20", "weight": ""},
        {"item": "Microphone", "quantity": "2", "weight": ""},
    ]
    assert details["dr_form"]["total_weight"] == ""


def test_non_equipment_table_is_ignored():
    assert scraper.parse_detail_documents([WAYBILL_TABLE])["dr_form"]["equipment"] == []


# ---- Tam belge ayrıştırma --------------------------------------------------

def test_waybill_and_dr_documents_merge_into_schema():
    details = scraper.parse_detail_documents([WAYBILL_TABLE, DR_FORM])
    assert details["waybill"] == {
        "tour_manager_name": "Elif Deneme", "tour_manager_phone": "+90 555 010 00 01",
        "tour_reference": "AUR-2611", "qv_id": "QV-700101", "client": "Aurora Reisen GmbH",
        "delivery_location": "Hotel Lale Sarayı\nKurgu Cad. No:7\n34122 Fatih / İstanbul",
        "delivery_phone": "+90 212 010 00 01",
        "return_location": "Haliç Demo Residence\nİskele Sok. No:4",
        "tour_start": "2026-11-25", "tour_end": "2026-12-01",
    }
    form = details["dr_form"]
    assert (form["customer"], form["reference"], form["qv_booking_id"]) == ("Aurora Reisen GmbH", "AUR-2611", "QV-700101")
    assert (form["clients"], form["groups"], form["instruction_language"]) == ("30", "1", "German")
    assert (form["service_start_date"], form["delivery_station"]) == ("2026-11-25", "Istanbul")
    assert form["qr_content"] == "QV-700101"


def test_empty_table_value_does_not_swallow_following_rows():
    html = """<table><tr><th>Tour Manager</th><td></td></tr><tr><th>Tour Reference</th><td>SV-311</td></tr>
              <tr><th>Delivery Location</th><td>Boğaz Demo Hotel<br>Sahil Yolu No:21</td></tr></table>"""
    waybill = scraper.parse_detail_documents([html])["waybill"]
    assert (waybill["tour_manager_name"], waybill["tour_manager_phone"]) == ("", "")
    assert waybill["tour_reference"] == "SV-311"
    assert waybill["delivery_location"] == "Boğaz Demo Hotel\nSahil Yolu No:21"


def test_empty_bare_label_line_stops_at_next_known_label():
    html = "<div>Tour Manager</div><div>Tour Reference SV-311</div><div>Client: Sample Voyages SA</div>"
    waybill = scraper.parse_detail_documents([html])["waybill"]
    assert waybill["tour_manager_name"] == "" and waybill["client"] == "Sample Voyages SA"


def test_missing_dr_fields_are_cross_filled_from_waybill():
    form = scraper.parse_detail_documents([WAYBILL_TABLE])["dr_form"]
    assert form["qv_booking_id"] == form["qr_content"] == "QV-700101"
    assert form["customer"] == "Aurora Reisen GmbH"
    assert form["clients"] == ""


def test_all_schema_values_are_strings_even_when_empty():
    details = scraper.parse_detail_documents(["<p>ilgisiz içerik</p>"])
    assert set(details["waybill"]) == set(scraper.WAYBILL_FIELDS)
    assert all(value == "" for value in details["waybill"].values())
    assert details["dr_form"]["equipment"] == [] and details["dr_form"]["total_weight"] == ""


# ---- Ağ beslemesi ve bağlantı keşfi ----------------------------------------

def test_extract_feed_events_finds_nested_event_arrays():
    payload = {"success": True, "data": {"events": [
        {"id": 7, "title": "Aurora Reisen", "start": "2026-11-25T09:30:00"},
        {"id": 8, "title": "Nordwind", "start": "2026-11-26"},
    ]}}
    events = scraper.extract_feed_events(payload)
    assert [(e["id"], e["start"]) for e in events] == [("7", "2026-11-25T09:30:00"), ("8", "2026-11-26")]
    assert scraper.extract_feed_events([{"name": "x"}, 3, "y"]) == []
    assert scraper.feed_date_time(events[0]) == ("2026-11-25", "09:30")
    assert scraper.feed_date_time(events[1]) == ("2026-11-26", "")


def test_match_feed_event_by_id_then_title():
    feed = scraper.extract_feed_events([
        {"id": 7, "title": "Aurora Reisen", "start": "2026-11-25T09:30:00"},
        {"id": 8, "title": "Nordwind", "start": "2026-11-26"},
    ])
    assert scraper.match_feed_event({"id": "8", "text": "?"}, feed)["start"] == "2026-11-26"
    assert scraper.match_feed_event({"id": "", "text": "09:30 Aurora Reisen", "title": ""}, feed)["id"] == "7"
    assert scraper.match_feed_event({"id": "", "text": "bilinmeyen"}, feed) is None


def test_match_calendar_links_uses_text_case_insensitively(settings):
    links = [
        {"text": "New Delivery", "href": "/orders/newDelivery.html"},
        {"text": "Calendar Delivery", "href": "/calendar/a.html"},
        {"text": "cruises  CALENDAR delivery", "href": "/calendar/b.html"},
        {"text": "Calendar Return", "href": "#"},
        {"text": "", "href": "/calendar/cruisesReturnCalendar.html"},
    ]
    found = scraper.match_calendar_links(links)
    assert found == {"delivery": "/calendar/a.html", "cruise_delivery": "/calendar/b.html",
                     "cruise_return": "/calendar/cruisesReturnCalendar.html"}
    spec = scraper.CALENDARS[0]
    urls = scraper.candidate_urls(spec, settings, found)
    assert urls[0] == "https://eu.quietvox.com/calendar/a.html"
    assert "https://eu.quietvox.com/calendar/deliveryCalendar.html" in urls


def test_candidate_urls_env_override_wins():
    custom = scraper.load_settings({"QV_URL_RETURN": "/x/ret.html", "QV_BASE_URL": "http://127.0.0.1:9"})
    spec = next(s for s in scraper.CALENDARS if s.key == "return")
    assert scraper.candidate_urls(spec, custom, {"return": "/menu.html"}) == ["http://127.0.0.1:9/x/ret.html"]


def test_selectors_can_be_overridden_from_environment():
    selectors = scraper.load_selectors({"QV_SEL_EVENT_PRIMARY": ".booking || a.item"})
    assert selectors["event_primary"] == [".booking", "a.item"]
    assert selectors["modal"] == scraper.SELECTORS["modal"]
