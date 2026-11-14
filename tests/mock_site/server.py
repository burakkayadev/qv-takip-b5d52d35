"""Quietvox'u taklit eden yerel sahte site (yalnızca testler için; tüm veriler KURGUSALDIR).

Katmanlar: HTTP Basic Auth -> HTML giriş formu -> ikinci doğrulama paneli -> oturum çerezi.
Dört takvim bilerek FARKLI yapıdadır ki kazıyıcının yedek katmanları sınansın:
  delivery         : hücre içi etkinlik + JSON feed + bootstrap modal (sekmeli, AJAX)
  cruise_delivery  : FullCalendar v3 benzeri iskelet (tarih geometriyle bulunur) + ui-dialog (dl)
  return           : bilinen yol + modal içinde yeni sekmede açılan D&R bağlantısı
  cruise_return    : menüde YOK (tahmini yol) + window.open ile açılan popup sayfası
"""
from __future__ import annotations

import base64
import html
import json
import threading
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

TEMPLATE_DIR = Path(__file__).resolve().parent
BASIC_USER, BASIC_PASS = "kapi-kullanici", "kapi-sifre-456"
FORM_USER, FORM_PASS = "test-kullanici", "test-sifre-123"

CALENDAR_PAGES = {
    "/calendar/deliveryCal.html": "delivery",
    "/calendar/cruiseDeliveryCal.html": "cruise_delivery",
    "/calendar/returnCalendar.html": "return",
    "/calendar/cruisesReturnCalendar.html": "cruise_return",
}
CALENDAR_UI = {
    "delivery": {"style": "cells", "source": "feed", "detail": "tabs"},
    "cruise_delivery": {"style": "v3", "source": "inline", "detail": "dialog"},
    "return": {"style": "cells", "source": "inline", "detail": "link"},
    "cruise_return": {"style": "cells", "source": "inline", "detail": "popup"},
}


def _equipment(receivers: int) -> tuple[list[tuple[str, str, str]], str]:
    rows = [
        ("QV9 TX EU", "2", "0.20 kg"),
        ("QV9 RX EU", str(receivers), f"{receivers * 0.07:.2f} kg"),
        ("Earphone", str(receivers + 2), f"{(receivers + 2) * 0.01:.2f} kg"),
        ("Carrying case", "1", "1.80 kg"),
    ]
    total = 0.20 + receivers * 0.07 + (receivers + 2) * 0.01 + 1.80
    return rows, f"{total:.2f} kg"


def _order(booking: str, client: str, manager: str, manager_phone: str, reference: str,
           delivery: list[str], delivery_phone: str, returning: list[str],
           start: int, end: int, receivers: int, language: str = "English") -> dict:
    rows, total = _equipment(receivers)
    return {
        "booking": booking, "client": client, "manager": manager, "manager_phone": manager_phone,
        "reference": reference, "delivery": delivery, "delivery_phone": delivery_phone,
        "returning": returning, "start": start, "end": end, "clients": str(receivers),
        "groups": "1", "language": language, "station": "Istanbul", "equipment": rows, "total": total,
    }


# Kurgusal siparişler (start/end: "bugün"e göre gün farkı)
ORDERS = {o["booking"]: o for o in (
    _order("QV-700101", "Aurora Reisen GmbH", "Elif Deneme", "+90 555 010 00 01", "AUR-2611",
           ["Hotel Lale Sarayı", "Kurgu Cad. No:7", "34122 Fatih / İstanbul"], "+90 212 010 00 01",
           ["Hotel Lale Sarayı", "Kurgu Cad. No:7", "34122 Fatih / İstanbul"], 0, 6, 30, "German"),
    _order("QV-700102", "Nordwind Tours Ltd.", "Kerem Sınama", "0555 010 00 02", "NWT-0042",
           ["Pera Köşk Otel", "Deneme Sok. No:12", "34430 Beyoğlu / İstanbul"], "+90 212 010 00 02",
           ["Marmara Demo Airport Hotel", "Havalimanı Yolu No:3", "Arnavutköy / İstanbul"], 1, 9, 18),
    _order("QV-700103", "Sample Voyages SA", "", "", "SV-311",
           ["Boğaz Demo Hotel", "Sahil Yolu No:21", "34349 Beşiktaş / İstanbul"], "",
           ["Boğaz Demo Hotel", "Sahil Yolu No:21"], 8, 15, 44),
    _order("QV-700104", "Pencere Dışı Travel", "Out Window", "+90 555 010 00 04", "OUT-1",
           ["Uzak Tarih Oteli"], "", ["Uzak Tarih Oteli"], 10, 20, 10),
    _order("QV-700105", "Geçmiş Travel", "Past Event", "+90 555 010 00 05", "OLD-1",
           ["Dünkü Otel"], "", ["Dünkü Otel"], -1, 3, 10),
    _order("QV-700201", "Demo Cruises Inc.", "Deniz Örnek", "+90 555 010 00 21", "CR-POYRAZ",
           ["Galata Demo Terminal", "Rıhtım Cad. No:1", "Beyoğlu / İstanbul"], "+90 212 010 00 21",
           ["Galata Demo Terminal"], 1, 1, 120),
    _order("QV-700202", "Demo Cruises Inc.", "Deniz Örnek", "+90 555 010 00 21", "CR-LODOS",
           ["Sarayburnu Demo Limanı", "Liman Sok. No:2"], "", ["Sarayburnu Demo Limanı"], 9, 9, 80),
    _order("QV-700203", "Demo Cruises Inc.", "", "", "CR-OUT", ["Uzak Liman"], "", ["Uzak Liman"], 12, 12, 20),
    _order("QV-700090", "Aurora Reisen GmbH", "Zeynep Kurgu", "+90 555 010 00 09", "AUR-2590",
           ["Hotel Lale Sarayı", "Kurgu Cad. No:7"], "+90 212 010 00 01",
           ["Haliç Demo Residence", "İskele Sok. No:4", "34087 Fatih / İstanbul"], -6, 1, 25, "German"),
    _order("QV-700301", "Demo Cruises Inc.", "Deniz Örnek", "+90 555 010 00 21", "CR-MELTEM",
           ["Galata Demo Terminal"], "", ["Karaköy Demo Rıhtımı", "Rıhtım Cad. No:9"], -3, 2, 60),
)}

# Takvim etkinlikleri: (sipariş, gün farkı, saat, başlık)
EVENTS = {
    "delivery": [
        ("QV-700101", 0, "09:30", "Aurora Reisen - Hotel Lale Sarayı"),
        ("QV-700102", 1, "", "Nordwind Tours - Pera Köşk Otel"),
        ("QV-700102", 1, "", "Nordwind Tours (2. grup) - Pera Köşk Otel"),  # aynı ID: tekilleşmeli
        ("QV-700103", 8, "14:00", "Sample Voyages - Boğaz Demo Hotel"),
        ("QV-700104", 10, "10:00", "Pencere dışı teslimat"),
        ("QV-700105", -1, "10:00", "Dünkü teslimat"),
    ],
    "cruise_delivery": [
        ("QV-700201", 1, "2p", "MS Poyraz - Galata Demo Terminal"),
        ("QV-700202", 9, "", "MS Lodos - Sarayburnu Demo Limanı"),
        ("QV-700203", 12, "9a", "Pencere dışı gemi"),
    ],
    "return": [
        ("QV-700090", 1, "11:00", "Aurora Reisen iade - Haliç Demo Residence"),
        ("QV-700101", 6, "", "Aurora Reisen iade - Hotel Lale Sarayı"),
    ],
    "cruise_return": [
        ("QV-700301", 2, "16:30", "MS Meltem iade - Karaköy Demo Rıhtımı"),
    ],
}

MENU = """
<ul class="nav"><li class="dropdown"><a href="#">D &amp; R Calendars</a><ul>
  <li><a href="/calendar/deliveryCal.html">Calendar Delivery</a></li>
  <li><a href="/calendar/cruiseDeliveryCal.html">Cruises  Calendar Delivery</a></li>
  <li><a href="/calendar/returnCalendar.html">CALENDAR RETURN</a></li>
</ul></li><li><a href="/orders/newDelivery.html">New Delivery</a></li></ul>
"""


def _page(title: str, body: str) -> str:
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title></head><body>{body}</body></html>"


def _login_page(action: str, heading: str, error: str = "") -> str:
    return _page(heading, f"""
<h1>{heading}</h1><p class="error">{html.escape(error)}</p>
<form method="post" action="{action}">
  <input type="hidden" name="csrf" value="kurgusal">
  <label>User <input type="text" name="username"></label>
  <label>Password <input type="password" name="password"></label>
  <button type="submit">Login</button>
</form>""")


def _rows(pairs: list[tuple[str, str]], label_tag: str = "th", colon: bool = False) -> str:
    suffix = ":" if colon else ""
    return "".join(f"<tr><{label_tag}>{html.escape(k)}{suffix}</{label_tag}><td>{v}</td></tr>" for k, v in pairs)


def _br(lines: list[str]) -> str:
    return "<br>".join(html.escape(line) for line in lines)


class Renderer:
    """Sipariş detaylarını takvime göre farklı HTML biçimlerinde üretir."""

    def __init__(self, today: date) -> None:
        self.today = today

    def day(self, offset: int) -> date:
        return self.today + timedelta(days=offset)

    def waybill_table(self, order: dict) -> str:
        """th/td tablosu; telefonlar değerlerin içine gömülü; tarih gg.aa.yyyy."""
        manager = f"{order['manager']} ({order['manager_phone']})" if order["manager"] else ""
        location = _br(order["delivery"])
        if order["delivery_phone"]:
            location += f"<br>Tel: {order['delivery_phone']}"
        return "<h4>WayBill</h4><table class='waybill'>" + _rows([
            ("Tour Manager", html.escape(manager)), ("Tour Reference", order["reference"]),
            ("QV ID", order["booking"]), ("Client", html.escape(order["client"])),
            ("Delivery Location", location), ("Return Location", _br(order["returning"])),
            ("Tour Start", self.day(order["start"]).strftime("%d.%m.%Y")),
            ("Tour End", self.day(order["end"]).strftime("%d.%m.%Y")), ("Internal Note", "kurgusal not"),
        ]) + "</table>"

    def equipment_table(self, order: dict, headers: tuple[str, str, str]) -> str:
        body = "".join(f"<tr><td>{i}</td><td>{q}</td><td>{w}</td></tr>" for i, q, w in order["equipment"])
        head = "".join(f"<th>{h}</th>" for h in headers)
        return (f"<table class='equipment'><thead><tr>{head}</tr></thead><tbody>{body}</tbody>"
                f"<tfoot><tr><td colspan='2'>Total</td><td>{order['total']}</td></tr></tfoot></table>")

    def dr_table(self, order: dict) -> str:
        """th/td tablosu + ekipman; tarih '25 Nov 2026' biçiminde."""
        start = self.day(order["start"]).strftime("%d %b %Y")
        return "<h4>Delivery and Return Form</h4><table class='dr'>" + _rows([
            ("Customer", html.escape(order["client"])), ("Reference", order["reference"]),
            ("QV Booking ID", order["booking"]), ("Clients", order["clients"]), ("Groups", order["groups"]),
            ("Instruction Language", order["language"]), ("Service Start Date", start),
            ("Delivery Station", order["station"]),
        ]) + "</table>" + self.equipment_table(order, ("Item", "Quantity", "Weight"))

    def waybill_dl(self, order: dict) -> str:
        """dl/dt/dd; telefonlar ayrı 'Phone' satırlarında; tarih ISO."""
        pairs = [
            ("Tour Manager", html.escape(order["manager"])), ("Phone", order["manager_phone"]),
            ("Tour Reference", order["reference"]), ("QV ID", order["booking"]),
            ("Client", html.escape(order["client"])), ("Delivery Location", _br(order["delivery"])),
            ("Phone", order["delivery_phone"]), ("Return Location", _br(order["returning"])),
            ("Tour Start", self.day(order["start"]).isoformat()), ("Tour End", self.day(order["end"]).isoformat()),
        ]
        return "<dl>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in pairs) + "</dl>"

    def dr_form_inputs(self, order: dict) -> str:
        """label + input alanları; ekipman başlıkları Article/Qty/Weight (kg)."""
        fields = [
            ("customer", "Customer", order["client"]), ("reference", "Reference", order["reference"]),
            ("booking", "QV Booking ID", order["booking"]), ("clients", "Clients", order["clients"]),
            ("groups", "Groups", order["groups"]), ("language", "Instruction Language", order["language"]),
            ("service", "Service Start Date", self.day(order["start"]).isoformat()),
            ("station", "Delivery Station", order["station"]),
        ]
        inputs = "".join(
            f"<div><label for='f-{key}'>{label}</label><input id='f-{key}' type='text' "
            f"value=\"{html.escape(value)}\" readonly></div>" for key, label, value in fields)
        return f"<div class='dr-form'>{inputs}{self.equipment_table(order, ('Article', 'Qty', 'Weight (kg)'))}</div>"

    def waybill_colon_table(self, order: dict) -> str:
        """td/td iki sütun, 'Etiket:' biçimi; tarih gg/aa/yyyy; D&R ayrı sayfada."""
        manager = f"{order['manager']}, Tel: {order['manager_phone']}" if order["manager"] else ""
        location = _br(order["delivery"] + ([order["delivery_phone"]] if order["delivery_phone"] else []))
        table = "<table>" + _rows([
            ("Tour Manager", html.escape(manager)), ("Tour Reference", order["reference"]),
            ("QV ID", order["booking"]), ("Client", html.escape(order["client"])),
            ("Delivery Location", location), ("Return Location", _br(order["returning"])),
            ("Tour Start", self.day(order["start"]).strftime("%d/%m/%Y")),
            ("Tour End", self.day(order["end"]).strftime("%d/%m/%Y")),
        ], label_tag="td", colon=True) + "</table>"
        link = f"<a href='/doc/dr.html?id={order['booking']}' target='_blank'>Delivery &amp; Return</a>"
        return table + link + "<button type='button' onclick='window.print()'>Print</button>"

    def dr_page(self, order: dict) -> str:
        """Dört sütunlu (etiket, değer, etiket, değer) tablo + Description/Amount/Weight ekipmanı."""
        start = self.day(order["start"]).strftime("%d/%m/%Y")
        cells = [
            ("Customer:", html.escape(order["client"]), "Reference:", order["reference"]),
            ("QV Booking ID:", order["booking"], "Clients:", order["clients"]),
            ("Groups:", order["groups"], "Instruction Language:", order["language"]),
            ("Service Start Date:", start, "Delivery Station:", order["station"]),
        ]
        rows = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in cells)
        return _page("D&R", f"<h1>Delivery and Return Form</h1><table>{rows}</table>"
                     + self.equipment_table(order, ("Description", "Amount", "Weight")))

    def full_page(self, order: dict) -> str:
        """Tablo yok: 'Etiket: değer' satırları; adres çok satırlı."""
        lines = [
            f"<div><b>Tour Manager:</b> {html.escape(order['manager'])} {order['manager_phone']}</div>",
            f"<div>Tour Reference: {order['reference']}</div>", f"<div>QV ID: {order['booking']}</div>",
            f"<div>Client: {html.escape(order['client'])}</div>",
            f"<div>Delivery Location: {_br(order['delivery'])}</div>",
            f"<div>Return Location:</div><div>{_br(order['returning'])}</div>",
            f"<div>Tour Start: {self.day(order['start']).isoformat()}</div>",
            f"<div>Tour End: {self.day(order['end']).isoformat()}</div>",
            f"<div>Customer: {html.escape(order['client'])}</div>", f"<div>Reference: {order['reference']}</div>",
            f"<div>QV Booking ID: {order['booking']}</div>", f"<div>Clients: {order['clients']}</div>",
            f"<div>Groups: {order['groups']}</div>", f"<div>Instruction Language: {order['language']}</div>",
            f"<div>Service Start Date: {self.day(order['start']).isoformat()}</div>",
            f"<div>Delivery Station: {order['station']}</div>",
        ]
        return _page("Order", "".join(lines) + self.equipment_table(order, ("Item", "Quantity", "Weight")))


class MockSite:
    """Sahte siteyi arka plan iş parçacığında çalıştırır ve test durumunu tutar."""

    def __init__(self, today: date) -> None:
        self.today = today
        self.renderer = Renderer(today)
        self.sessions: set[str] = set()
        self.login_posts = 0
        self.reject_form_login = False
        self.broken_calendars: set[str] = set()
        self.notifications: list[dict] = []
        self.requests: list[str] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_class())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> "MockSite":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def reset(self) -> None:
        self.sessions.clear()
        self.login_posts = 0
        self.reject_form_login = False
        self.broken_calendars.clear()
        self.notifications.clear()
        self.requests.clear()

    def events_for(self, calendar: str) -> list[dict]:
        events = []
        for index, (booking, offset, time_text, title) in enumerate(EVENTS[calendar]):
            events.append({"id": str(9000 + index), "key": booking, "title": title, "time": time_text,
                           "date": self.renderer.day(offset).isoformat()})
        return events

    def calendar_page(self, calendar: str) -> str:
        ui = CALENDAR_UI[calendar]
        config = {"cal": calendar, "today": self.today.isoformat(), **ui,
                  "events": self.events_for(calendar) if ui["source"] == "inline" else []}
        template = (TEMPLATE_DIR / "calendar.html").read_text(encoding="utf-8")
        return template.replace("__MENU__", MENU).replace("__CONFIG__", json.dumps(config, ensure_ascii=False))

    def _handler_class(self) -> type:
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # test çıktısını kirletme
                return

            def do_GET(self) -> None:
                route(site, self, "GET")

            def do_POST(self) -> None:
                route(site, self, "POST")

        return Handler


def _send(handler: BaseHTTPRequestHandler, status: int, body: str = "", content_type: str = "text/html",
          headers: dict | None = None) -> None:
    data = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", f"{content_type}; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(data)


def _redirect(handler: BaseHTTPRequestHandler, location: str, cookie: str = "") -> None:
    headers = {"Location": location}
    if cookie:
        headers["Set-Cookie"] = f"{cookie}; Path=/"
    _send(handler, 302, "", headers=headers)


def _cookies(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    pairs = (part.strip().split("=", 1) for part in (handler.headers.get("Cookie") or "").split(";") if "=" in part)
    return {name: value for name, value in pairs}


def _basic_ok(handler: BaseHTTPRequestHandler) -> bool:
    expected = base64.b64encode(f"{BASIC_USER}:{BASIC_PASS}".encode()).decode()
    return handler.headers.get("Authorization") == f"Basic {expected}"


def _form(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    length = int(handler.headers.get("Content-Length") or 0)
    return {k: v[0] for k, v in parse_qs(handler.rfile.read(length).decode("utf-8")).items()}


def _handle_post(site: MockSite, handler: BaseHTTPRequestHandler, path: str) -> None:
    if path.startswith("/ntfy/"):
        length = int(handler.headers.get("Content-Length") or 0)
        site.notifications.append({"topic": path.split("/", 2)[2], "body": handler.rfile.read(length).decode("utf-8"),
                                   "title": handler.headers.get("Title", "")})
        return _send(handler, 200, "{}", "application/json")
    form = _form(handler)
    site.login_posts += 1
    valid = (form.get("username"), form.get("password")) == (FORM_USER, FORM_PASS) and not site.reject_form_login
    if path == "/login":
        if valid:
            return _redirect(handler, "/verify.html", "qvstage=1")
        return _send(handler, 200, _login_page("/login", "Login", "Invalid credentials"))
    if path == "/verify" and valid and _cookies(handler).get("qvstage") == "1":
        token = f"oturum-{len(site.sessions) + 1}"
        site.sessions.add(token)
        return _redirect(handler, "/home.html", f"qvsession={token}")
    return _send(handler, 200, _login_page("/verify", "Security check", "Invalid credentials"))


def _handle_protected(site: MockSite, handler: BaseHTTPRequestHandler, path: str, query: dict) -> None:
    order = ORDERS.get((query.get("id") or [""])[0])
    render = site.renderer
    if path == "/home.html":
        return _send(handler, 200, _page("Home", MENU + "<h1>Welcome</h1>"))
    if path in CALENDAR_PAGES:
        calendar = CALENDAR_PAGES[path]
        if calendar in site.broken_calendars:
            return _send(handler, 500, "Internal Server Error")
        return _send(handler, 200, site.calendar_page(calendar))
    if path == "/api/events":
        events = [{"id": e["id"], "title": e["title"], "key": e["key"],
                   "start": e["date"] + (f"T{e['time']}:00" if e["time"] else "")}
                  for e in site.events_for(query["cal"][0])]
        return _send(handler, 200, json.dumps(events, ensure_ascii=False), "application/json")
    if order is None:
        return _send(handler, 404, _page("Not Found", "<h1>404</h1>"))
    fragments = {
        ("/api/detail", "waybill"): render.waybill_table, ("/api/detail", "dr"): render.dr_table,
        ("/api/detail", "waybill_dl"): render.waybill_dl, ("/api/detail", "dr_inputs"): render.dr_form_inputs,
        ("/api/detail", "waybill_colon"): render.waybill_colon_table,
        ("/doc/dr.html", ""): render.dr_page, ("/doc/full.html", ""): render.full_page,
    }
    builder = fragments.get((path, (query.get("part") or [""])[0]))
    if builder is None:
        return _send(handler, 404, _page("Not Found", "<h1>404</h1>"))
    _send(handler, 200, builder(order))


def route(site: MockSite, handler: BaseHTTPRequestHandler, method: str) -> None:
    """Tüm isteklerin giriş noktası: Basic Auth -> giriş akışı -> oturum korumalı sayfalar."""
    parts = urlsplit(handler.path)
    path, query = parts.path, parse_qs(parts.query)
    site.requests.append(f"{method} {path}")
    if method == "POST" and path.startswith("/ntfy/"):
        return _handle_post(site, handler, path)
    if not _basic_ok(handler):
        return _send(handler, 401, "Unauthorized", headers={"WWW-Authenticate": 'Basic realm="Quietvox Mock"'})
    if method == "POST":
        return _handle_post(site, handler, path)
    if path == "/login.html":
        return _send(handler, 200, _login_page("/login", "Login"))
    if path == "/verify.html":
        return _send(handler, 200, _login_page("/verify", "Security check"))
    if _cookies(handler).get("qvsession") not in site.sessions:
        return _redirect(handler, "/login.html")
    _handle_protected(site, handler, path, query)
