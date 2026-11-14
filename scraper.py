#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quietvox sipariş takip sistemi — arka plan kazıyıcısı.

Akış: giriş (HTTP Basic + HTML formu) -> 4 takvim -> etkinlik detayları (modal)
-> tekilleştirme/sıralama -> data.json -> (isteğe bağlı) ntfy hatırlatması.

Dosya düzeni:
  1) Yapılandırma (seçiciler, takvimler, ortam değişkenleri)
  2) Saf ayrıştırma fonksiyonları (Playwright gerektirmez, test edilebilir)
  3) Sipariş kurma, tekilleştirme, belge birleştirme
  4) Dosya yazımı ve bildirimler
  5) Playwright katmanı (giriş, takvim, modal)
  6) Orkestrasyon ve main()
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

log = logging.getLogger("quietvox")

# ============================================================================
# 1) YAPILANDIRMA — canlı siteye göre ayar gerekirse YALNIZCA bu bölüm değişir
# ============================================================================

DEFAULT_BASE_URL = "https://eu.quietvox.com"
DEFAULT_LOGIN_PATH = "/login.html"
DEFAULT_NTFY_URL = "https://ntfy.sh"
TIMEZONE_NAME = "Europe/Istanbul"
SCHEMA_VERSION = 1
DEFAULT_WINDOW_DAYS = 10

MAX_LOGIN_ROUNDS = 3  # çift doğrulama panelleri için en fazla form turu
MAX_NEXT_CLICKS = 2  # takvimde en fazla kaç kez "sonraki" tıklanır
MAX_SUB_DOCUMENTS = 5  # modal içindeki WayBill / D&R / Print bağlantı sınırı
MAX_DEBUG_MODALS = 3  # QV_DEBUG=1 iken takvim başına dökülecek modal sayısı
MAX_RAW_ENTRIES = 40  # raw sözlüğüne girecek tanınmayan etiket sınırı
MAX_NAMES_IN_REMINDER = 4
FRESHNESS_SECONDS = 2 * 60 * 60  # içerik aynıysa bu süreden önce yeniden yazma
NTFY_TIMEOUT_SECONDS = 15
GOTO_ATTEMPTS = 2
SETTLE_TIMEOUT_MS = 5000
CLICK_TIMEOUT_MS = 3000

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_CONFIG = 2
EXIT_LOGIN = 3

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_LOGIN_FAILED = "login_failed"

LOGIN_FAILED_MESSAGE = "⚠️ Quietvox oturumu açılamadı, şifreyi kontrol edin"

# CSS seçici adayları. Her anahtar QV_SEL_<ANAHTAR> ortam değişkeniyle ezilebilir
# (birden çok aday "||" ile ayrılır). Örn: QV_SEL_EVENT_PRIMARY=".my-event||a.booking"
SELECTORS: dict[str, list[str]] = {
    "login_user": [
        "input[type=email]", "input[name*=user i]", "input[id*=user i]",
        "input[name*=mail i]", "input[id*=mail i]", "input[name*=login i]",
        "input[id*=login i]", "input[type=text]", "input:not([type])",
    ],
    "login_password": ["input[type=password]"],
    "login_submit": [
        "button[type=submit]", "input[type=submit]", "button:has-text('Login')",
        "button:has-text('Log in')", "button:has-text('Sign in')",
        "button:has-text('Anmelden')", "input[type=button]", "button",
    ],
    "calendar_root": [".fc", "#calendar", ".calendar", "[data-date]"],
    "event_primary": [
        ".fc-event", "a.fc-day-grid-event", ".fc-daygrid-event",
        ".fc-list-item", ".fc-list-event",
    ],
    # Genel yedek: takvim hücresi içindeki tıklanabilir öğeler
    "event_fallback": [
        "[data-date] a", "[data-date] [onclick]", "#calendar td a",
        "#calendar td [onclick]", ".calendar td a", ".calendar td [onclick]",
    ],
    "event_time": [".fc-time", ".fc-event-time", ".fc-list-item-time", ".fc-list-event-time"],
    "event_title": [".fc-title", ".fc-event-title", ".fc-list-item-title", ".fc-list-event-title"],
    "day_cell": ["td[data-date]", ".fc-day[data-date]", "[data-date]"],
    "next_button": [
        ".fc-next-button", ".fc-button-next", "button[aria-label*=next i]",
        "a[aria-label*=next i]", "[title*=next i]", "button:has-text('›')",
        "button:has-text('>')", "a:has-text('›')", ".next",
    ],
    "modal": [
        ".modal.show", ".modal.in", ".ui-dialog", "[role=dialog]", "dialog[open]",
        ".modal[style*='display: block']", ".fancybox-content",
    ],
    "modal_close": [
        "[data-dismiss=modal]", "[data-bs-dismiss=modal]", ".btn-close", ".close",
        ".ui-dialog-titlebar-close", "button:has-text('Close')",
        "button:has-text('Schließen')", "button:has-text('Kapat')",
    ],
    "sub_link": ["a", "button", "[role=tab]", "input[type=button]"],
}

# Modal içinde ayrıca açılacak belge bağlantılarının metin desenleri
# (QV_SUB_LINK_PATTERNS ile "||" ayracıyla ezilebilir).
SUB_LINK_PATTERNS = [
    r"way\s*-?\s*bill",
    r"delivery\s*(?:and|&|\+)\s*return",
    r"\bd\s*(?:&|and)\s*r\b",
    r"\bprint\b",
]


@dataclass(frozen=True)
class CalendarSpec:
    """Tek bir takvimin tanımı."""

    key: str
    name: str  # data.json içindeki source_calendar değeri (menü metni)
    kind: str  # "delivery" | "return"
    cruise: bool
    env_var: str  # URL'yi ezen ortam değişkeni
    paths: tuple[str, ...]  # menüden bulunamazsa denenecek tahmini yollar


CALENDARS: tuple[CalendarSpec, ...] = (
    CalendarSpec("delivery", "Calendar Delivery", "delivery", False, "QV_URL_DELIVERY", (
        "/calendar/deliveryCalendar.html", "/calendar/calendarDelivery.html",
        "/calendar/delivery.html", "/calendar/deliveryCalendar",
    )),
    CalendarSpec("cruise_delivery", "Cruises Calendar Delivery", "delivery", True,
                 "QV_URL_CRUISE_DELIVERY", (
        "/calendar/cruisesDeliveryCalendar.html", "/calendar/cruiseDeliveryCalendar.html",
        "/calendar/cruisesCalendarDelivery.html", "/calendar/deliveryCruisesCalendar.html",
        "/calendar/deliveryCalendarCruises.html",
    )),
    # Bilinen (doğrulanmış) tek yol: /calendar/returnCalendar.html
    CalendarSpec("return", "Calendar Return", "return", False, "QV_URL_RETURN", (
        "/calendar/returnCalendar.html",
    )),
    CalendarSpec("cruise_return", "Cruises Calendar Return", "return", True,
                 "QV_URL_CRUISE_RETURN", (
        "/calendar/cruisesReturnCalendar.html", "/calendar/cruiseReturnCalendar.html",
        "/calendar/cruisesCalendarReturn.html", "/calendar/returnCruisesCalendar.html",
        "/calendar/returnCalendarCruises.html",
    )),
)
KNOWN_PROTECTED_PATH = "/calendar/returnCalendar.html"

# Canlı sitede doğrulanan doğrudan uçlar (tıklamasız yol)
FEED_FILE = "getContent.html"
WAYBILL_PATH = "/order/wayBill"
DR_FORM_PATH = "/order/deliveryReturnFormView"
DR_EQUIPMENT_TABLE_ID = "delivered-retruned"  # sitedeki yazım hatası aynen korunur
DIRECT_CONCURRENCY = 4  # siteyi yormamak için aynı anda en fazla istek
DIRECT_SKIPPED_ROWS = {"bag id", "clients"}  # ekipman tablosunda kalem olmayan satırlar
DIRECT_LABELS: dict[str, tuple[str, str]] = {
    "cell phone": ("waybill", "tour_manager_phone"),
    "service end date": ("waybill", "tour_end"),
}
LIST_KEY_BY_KIND = {"delivery": "deliveries", "return": "returns"}

WAYBILL_FIELDS = (
    "tour_manager_name", "tour_manager_phone", "tour_reference", "qv_id", "client",
    "delivery_location", "delivery_phone", "return_location", "tour_start", "tour_end",
)
DR_FIELDS = (
    "customer", "reference", "qv_booking_id", "clients", "groups",
    "instruction_language", "service_start_date", "delivery_station", "qr_content",
)

# Etiket eşanlamlıları: (bölüm, alan) -> olası etiket metinleri (normalize edilerek eşlenir)
FIELD_SYNONYMS: dict[tuple[str, str], tuple[str, ...]] = {
    ("waybill", "tour_manager"): ("tour manager", "tourmanager", "tour leader", "tour guide", "guide"),
    ("waybill", "tour_manager_phone"): ("tour manager phone", "tour manager mobile", "tm phone", "tour manager tel"),
    ("waybill", "tour_reference"): ("tour reference", "tour ref", "tour no", "tour number", "tour"),
    ("waybill", "qv_id"): ("qv id", "qvid", "quietvox id", "qv no"),
    ("waybill", "client"): ("client",),
    ("waybill", "delivery_location"): ("delivery location", "delivery address", "deliver to", "delivery"),
    ("waybill", "delivery_phone"): ("delivery phone", "delivery location phone", "delivery tel"),
    ("waybill", "return_location"): ("return location", "return address", "pickup location", "pick up location", "collection location", "return"),
    ("waybill", "tour_start"): ("tour start", "tour start date", "start date", "start"),
    ("waybill", "tour_end"): ("tour end", "tour end date", "end date", "end"),
    ("dr_form", "customer"): ("customer",),
    ("dr_form", "reference"): ("reference", "ref", "customer reference"),
    ("dr_form", "qv_booking_id"): ("qv booking id", "booking id", "qv booking no", "booking no", "booking number", "qv booking"),
    ("dr_form", "clients"): ("clients", "number of clients", "no of clients", "pax", "participants"),
    ("dr_form", "groups"): ("groups", "number of groups", "no of groups"),
    ("dr_form", "instruction_language"): ("instruction language", "language", "instructions language"),
    ("dr_form", "service_start_date"): ("service start date", "service start"),
    ("dr_form", "delivery_station"): ("delivery station", "station"),
    ("dr_form", "total_weight"): ("total weight", "weight total"),
    ("meta", "delivery_date"): ("delivery date",),
    ("meta", "delivery_time"): ("delivery time",),
    ("meta", "return_date"): ("return date", "pickup date", "pick up date"),
    ("meta", "return_time"): ("return time", "pickup time", "pick up time"),
    ("meta", "date"): ("date",),
    ("meta", "time"): ("time",),
}
LABEL_MAP: dict[str, tuple[str, str]] = {
    synonym: target for target, synonyms in FIELD_SYNONYMS.items() for synonym in synonyms
}
PHONE_LABELS = {"phone", "tel", "telephone", "telefon", "mobile", "gsm", "phone number", "cell"}
MULTILINE_FIELDS = {"tour_manager", "delivery_location", "return_location"}
SAFE_BARE_LABELS = {"client", "customer", "clients", "groups", "reference"}
MULTILINE_MAX_LINES = 6

EQUIPMENT_HEADERS: dict[str, tuple[str, ...]] = {
    "item": ("item", "article", "description", "equipment", "product", "artikel"),
    "quantity": ("quantity", "qty", "amount", "pcs", "pieces", "menge", "anzahl"),
    "weight": ("weight", "gewicht"),
}


@dataclass(frozen=True)
class Settings:
    """Ortam değişkenlerinden okunan çalışma ayarları."""

    base_url: str
    login_url: str
    user: str
    password: str
    http_user: str
    http_pass: str
    output: Path
    cookies: Path
    debug: bool
    debug_dir: Path
    headless: bool
    prefer_click: bool
    ntfy_topic: str
    ntfy_url: str
    send_reminder: bool
    today: date
    window_days: int
    nav_timeout: int
    modal_timeout: int
    url_overrides: dict[str, str]
    selectors: dict[str, list[str]]
    sub_link_patterns: list[str]
    # Doğrudan uçlar çalışmazsa takvime tıklayan eski yola düşülsün mü?
    # Gerçek sitede kapalıdır: boş güne tıklamak "Create Order" penceresini açar.
    click_fallback: bool = True


def _env(env: Mapping[str, str], name: str, default: str = "") -> str:
    return (env.get(name) or "").strip() or default


def _env_flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = _env(env, name)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on", "evet"}


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(_env(env, name, str(default)))
    except ValueError:
        return default


def _env_list(env: Mapping[str, str], name: str, default: list[str]) -> list[str]:
    value = _env(env, name)
    if not value:
        return list(default)
    return [part.strip() for part in value.split("||") if part.strip()]


def load_selectors(env: Mapping[str, str]) -> dict[str, list[str]]:
    """Varsayılan seçicileri QV_SEL_<ANAHTAR> ortam değişkenleriyle ezer."""
    return {key: _env_list(env, f"QV_SEL_{key.upper()}", default) for key, default in SELECTORS.items()}


def istanbul_today(env: Mapping[str, str]) -> date:
    """İstanbul saatine göre bugün. QV_TODAY (YYYY-AA-GG) test/ayıklama için ezer."""
    override = _env(env, "QV_TODAY")
    if override:
        return date.fromisoformat(override)
    return datetime.now(ZoneInfo(TIMEZONE_NAME)).date()


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Tüm ayarları ortam değişkenlerinden okur."""
    env = os.environ if env is None else env
    base_dir = Path(__file__).resolve().parent
    base_url = _env(env, "QV_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    user, password = _env(env, "QV_USER"), env.get("QV_PASS") or ""
    return Settings(
        base_url=base_url,
        login_url=urllib.parse.urljoin(base_url + "/", _env(env, "QV_LOGIN_PATH", DEFAULT_LOGIN_PATH)),
        user=user,
        password=password,
        http_user=_env(env, "QV_HTTP_USER", user),
        http_pass=(env.get("QV_HTTP_PASS") or "") or password,
        output=Path(_env(env, "QV_OUTPUT", str(base_dir / "data.json"))),
        cookies=Path(_env(env, "QV_COOKIES", str(base_dir / "cookies.json"))),
        debug=_env_flag(env, "QV_DEBUG", False),
        debug_dir=Path(_env(env, "QV_DEBUG_DIR", str(base_dir / "debug"))),
        headless=_env_flag(env, "HEADLESS", True),
        prefer_click=_env_flag(env, "QV_PREFER_CLICK", False),
        ntfy_topic=_env(env, "NTFY_TOPIC"),
        ntfy_url=_env(env, "NTFY_URL", DEFAULT_NTFY_URL),
        send_reminder=_env_flag(env, "SEND_REMINDER", False),
        today=istanbul_today(env),
        window_days=max(1, _env_int(env, "QV_WINDOW_DAYS", DEFAULT_WINDOW_DAYS)),
        nav_timeout=_env_int(env, "QV_NAV_TIMEOUT_MS", 30000),
        modal_timeout=_env_int(env, "QV_MODAL_TIMEOUT_MS", 8000),
        url_overrides={spec.key: _env(env, spec.env_var) for spec in CALENDARS},
        selectors=load_selectors(env),
        sub_link_patterns=_env_list(env, "QV_SUB_LINK_PATTERNS", SUB_LINK_PATTERNS),
        click_fallback=_env_flag(env, "QV_CLICK_FALLBACK", base_url != DEFAULT_BASE_URL),
    )


# ============================================================================
# 2) SAF AYRIŞTIRMA FONKSİYONLARI (Playwright'sız)
# ============================================================================

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "mär": 3, "mai": 5, "okt": 10, "dez": 12,  # Almanca
    "oca": 1, "şub": 2, "sub": 2, "nis": 4, "haz": 6, "tem": 7, "ağu": 8, "agu": 8,
    "eyl": 9, "eki": 10, "kas": 11, "ara": 12,  # Türkçe
}
_WORD = r"[A-Za-zÀ-ÿçğıöşüÇĞİÖŞÜ]+"
_DATE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)"), "ymd"),
    (re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?!\d)"), "dmy"),
    (re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{2})(?!\d)"), "dmy2"),
    (re.compile(rf"(?<!\d)(\d{{1,2}})\.?\s+({_WORD})\.?,?\s+(\d{{4}})(?!\d)"), "dMy"),
    (re.compile(rf"({_WORD})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})(?!\d)"), "Mdy"),
)
_TIME_RE = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?::\d{2})?\s*(am|pm|a|p)?\b", re.I)
_HOUR_ONLY_RE = re.compile(r"(?<![\d:.])(\d{1,2})\s*(am|pm|a|p)\b", re.I)
_CLOCK = r"\d{1,2}(?::\d{2}(?:\s*(?:am|pm|a|p)\b)?|\s*(?:am|pm|a|p)\b)"
_TIME_PREFIX_RE = re.compile(rf"^\s*{_CLOCK}(?:\s*[-–]\s*{_CLOCK})?\s*", re.I)

_PHONE_LABEL = r"(?:tel(?:efon|ephone)?|phone|mobile|mobil|gsm|cell|handy|fon)\b\.?\s*(?:no\.?|number)?\s*[:.]?\s*"
_PHONE_NUMBER = r"(?:\+|00)?\(?\d[\d ()./\-]{5,}\d"
_PHONE_RE = re.compile(rf"({_PHONE_LABEL})?({_PHONE_NUMBER})", re.I)
_TOTAL_RE = re.compile(r"^(?:grand\s+)?(?:total|sum|gesamt|summe|toplam)\b", re.I)
# Etiket satırı: iki noktadan sonra boşluk ya da satır sonu gelmeli ("No:7" etiket değildir)
_LINE_RE = re.compile(r"^([^\d:\n]{2,45}?)\s*:(?:\s+(.*))?$")
_BLOCK_TAGS = (
    "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd",
    "address", "section", "table", "ul", "ol", "fieldset",
)
_SKIPPED_INPUT_TYPES = {
    "hidden", "password", "submit", "button", "checkbox", "radio", "file", "image", "reset",
}


def clean_text(text: Any) -> str:
    """Satır içi boşlukları sadeleştirir, boş satırları atar; satır sonlarını korur."""
    lines = (re.sub(r"[ \t\r\f\v\u00a0]+", " ", line).strip() for line in str(text or "").split("\n"))
    return "\n".join(line for line in lines if line)


def one_line(text: Any) -> str:
    """Metni tek satıra indirger."""
    return " ".join(clean_text(text).split("\n"))


def norm_label(label: Any) -> str:
    """Etiketi eşleme anahtarına çevirir: küçük harf, iki nokta/noktalama/boşluk toleranslı."""
    return re.sub(r"[^a-z0-9]+", " ", one_line(label).casefold()).strip()


def valid_iso(text: Any) -> str:
    """Metin geçerli bir YYYY-AA-GG tarihiyse onu, değilse boş string döner."""
    text = str(text or "").strip()
    if not _ISO_RE.match(text):
        return ""
    try:
        date.fromisoformat(text)
    except ValueError:
        return ""
    return text


def _month_number(word: str) -> int:
    return _MONTHS.get(word.lower()[:3], 0)


def _date_from_match(match: re.Match[str], layout: str) -> str:
    """Desen eşleşmesinden ISO tarih üretir; geçersizse boş string."""
    a, b, c = match.groups()
    if layout == "ymd":
        year, month, day = int(a), int(b), int(c)
    elif layout == "dmy":
        day, month, year = int(a), int(b), int(c)
    elif layout == "dmy2":
        day, month, year = int(a), int(b), 2000 + int(c)
    elif layout == "dMy":
        day, month, year = int(a), _month_number(b), int(c)
    else:  # "Mdy"
        month, day, year = _month_number(a), int(b), int(c)
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def normalize_date(text: Any) -> str:
    """Tarihi YYYY-AA-GG'ye çevirir; ayrıştırılamazsa ham metni (tek satır) bırakır."""
    raw = one_line(text)
    for pattern, layout in _DATE_PATTERNS:
        for match in pattern.finditer(raw):
            iso = _date_from_match(match, layout)
            if iso:
                return iso
    return raw


def extract_time(text: Any) -> str:
    """Metindeki ilk saati HH:MM olarak döner; yoksa boş string. Tarih kısımları yok sayılır."""
    raw = one_line(text)
    for pattern, _layout in _DATE_PATTERNS:
        raw = pattern.sub(" ", raw)
    match = _TIME_RE.search(raw)
    minute = 0
    if match:
        hour, minute, suffix = int(match.group(1)), int(match.group(2)), match.group(3)
    else:
        match = _HOUR_ONLY_RE.search(raw)
        if not match:
            return ""
        hour, suffix = int(match.group(1)), match.group(2)
    if suffix:
        is_pm = suffix.lower().startswith("p")
        hour = (hour % 12) + (12 if is_pm else 0)
    if hour > 23 or minute > 59:
        return ""
    return f"{hour:02d}:{minute:02d}"


def _pick_phone(text: str) -> re.Match[str] | None:
    """Metindeki en olası telefon eşleşmesini seçer (etiketli > +/00 > tek satır > uzun)."""
    candidates = []
    for match in _PHONE_RE.finditer(text):
        digits = sum(ch.isdigit() for ch in match.group(2))
        if 8 <= digits <= 15:
            candidates.append((match, digits))
    lines = {line.strip() for line in text.split("\n")}
    rules = (
        lambda m, d: bool(m.group(1)),
        lambda m, d: m.group(2).startswith(("+", "00")),
        lambda m, d: d >= 9 and m.group(0).strip() in lines,
        lambda m, d: d >= 10,
    )
    for rule in rules:
        for match, digits in candidates:
            if rule(match, digits):
                return match
    return None


def split_phone(text: Any) -> tuple[str, str]:
    """Metinden telefon numarasını ayırır: (telefonsuz metin, telefon)."""
    cleaned = clean_text(text)
    match = _pick_phone(cleaned)
    if not match:
        return cleaned, ""
    phone = match.group(2).strip()
    if phone.count("(") != phone.count(")"):
        phone = phone.strip("()").strip()
    start, end = match.span()
    opened_inside = match.group(0).count("(") > match.group(0).count(")")
    if opened_inside and cleaned[end:end + 1] == ")":
        end += 1  # "(0555 ...)" biçiminde kapanış parantezi eşleşmenin dışında kalır
    rest = re.sub(r"\([ \t]*\)", "", cleaned[:start] + cleaned[end:])
    rest_lines = (line.strip(" ,;:-/|") for line in rest.split("\n"))
    return "\n".join(line for line in rest_lines if line.strip("() ")), phone


def _control_value(element: Any) -> str | None:
    """Form denetiminin görünen değerini döner; görünmeyen türler için None."""
    if element.name == "textarea":
        return element.get_text()
    if element.name == "select":
        option = element.find("option", selected=True)
        return option.get_text() if option else ""
    if (element.get("type") or "text").lower() in _SKIPPED_INPUT_TYPES:
        return None
    return element.get("value") or ""


def _flatten_controls(soup: BeautifulSoup) -> None:
    """input/textarea/select öğelerini değerlerini taşıyan span'lere çevirir."""
    for element in soup.find_all(["input", "textarea", "select"]):
        value = _control_value(element)
        if value is None:
            element.decompose()
            continue
        span = soup.new_tag("span")
        span["data-qv-val"] = "1"
        if element.get("id"):
            span["id"] = element["id"]
        span.string = value
        element.replace_with(span)


def prepare_soup(html: str) -> BeautifulSoup:
    """HTML'i metin çıkarımına hazırlar: betikleri atar, satır sonlarını görünür kılar."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    _flatten_controls(soup)
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for cell in soup.find_all(["td", "th"]):
        cell.append("\t")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before("\n")
        tag.append("\n")
    return soup


_USED = "data-qv-used"  # yapısal stratejilerin tükettiği öğelerin işareti
_MAX_CONSUMED_VALUE = 300


def _looks_like_label(cell: Any, text: str) -> bool:
    return cell.name == "th" or text.endswith(":") or norm_label(text) in LABEL_MAP


def _table_pairs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Tablo satırlarından etiket/değer çiftleri: th/td, td/td ve 4+ sütunlu çiftli düzen."""
    pairs = []
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        texts = [clean_text(cell.get_text()) for cell in cells]
        if len(cells) == 2 and cells[1].name == "td":
            pairs.append((texts[0], texts[1]))
            row[_USED] = "1"
            continue
        if len(cells) < 4 or len(cells) % 2:
            continue
        labels_ok = all(_looks_like_label(cells[i], texts[i]) for i in range(0, len(cells), 2))
        values_ok = all(cells[i].name == "td" for i in range(1, len(cells), 2))
        if labels_ok and values_ok:
            pairs.extend((texts[i], texts[i + 1]) for i in range(0, len(cells), 2))
            row[_USED] = "1"
    return pairs


def _dl_pairs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """dl/dt/dd yapılarından etiket/değer çiftleri."""
    pairs = []
    for term in soup.find_all("dt"):
        definition = term.find_next_sibling(["dd", "dt"])
        if definition is not None and definition.name == "dd":
            pairs.append((clean_text(term.get_text()), clean_text(definition.get_text())))
            term[_USED] = definition[_USED] = "1"
    return pairs


def _label_pairs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """label + input/span yapılarından etiket/değer çiftleri."""
    pairs = []
    for label in soup.find_all("label"):
        inner = label.find(attrs={"data-qv-val": True})
        target = soup.find(id=label["for"]) if label.get("for") else None
        name = "".join(text for text in label.strings if inner is None or inner not in text.parents)
        if inner is not None:
            value = inner.get_text()
        elif target is not None:
            value = target.get_text()
        else:
            target = label.find_next_sibling()
            value = target.get_text() if target is not None else ""
        pairs.append((clean_text(name), clean_text(value)))
        label[_USED] = "1"
        if target is not None and len(value) <= _MAX_CONSUMED_VALUE:
            target[_USED] = "1"
    return pairs


def _split_label_line(line: str) -> tuple[str | None, str]:
    """'Etiket: değer' satırını ayırır; etiket satırı değilse (None, '')."""
    match = _LINE_RE.match(line)
    if match:
        return match.group(1).strip(), (match.group(2) or "").strip()
    key = norm_label(line)
    if key in LABEL_MAP and (len(key.split()) >= 2 or key in SAFE_BARE_LABELS):
        return line, ""
    return None, ""


def _starts_new_field(line: str) -> bool:
    """Satır bir etiket satırı mı ya da bilinen (2+ kelimeli) bir etiketle mi başlıyor?"""
    if _split_label_line(line)[0] is not None:
        return True
    words = norm_label(line).split()
    return any(" ".join(words[:size]) in LABEL_MAP for size in range(2, min(len(words), 4) + 1))


def _line_pairs(text: str) -> list[tuple[str, str]]:
    """Düz metindeki 'Etiket: değer' satırlarından çiftler; adresler çok satırlı toplanır."""
    lines = clean_text(text).split("\n")
    pairs, index = [], 0
    while index < len(lines):
        label, value = _split_label_line(lines[index])
        index += 1
        if label is None:
            continue
        target = LABEL_MAP.get(norm_label(label))
        if target and target[1] in MULTILINE_FIELDS:
            limit = MULTILINE_MAX_LINES
        else:
            limit = 0 if value else 1
        extra: list[str] = []
        while index < len(lines) and len(extra) < limit and not _starts_new_field(lines[index]):
            extra.append(lines[index])
            index += 1
        pairs.append((label, "\n".join([value, *extra]).strip()))
    return pairs


def _pairs_from_soup(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Önce yapısal stratejiler; satır stratejisi yalnızca onların tüketmediği metinde çalışır."""
    pairs = _table_pairs(soup) + _dl_pairs(soup) + _label_pairs(soup)
    for element in soup.find_all(attrs={_USED: True}):
        if not element.decomposed:
            element.decompose()
    return pairs + _line_pairs(soup.get_text(" "))


def extract_pairs(html: str) -> list[tuple[str, str]]:
    """HTML/metinden tüm etiket→değer çiftlerini çıkarır (tablo, dl, label, satır)."""
    return _pairs_from_soup(prepare_soup(html))


def _equipment_columns(cells: list[str]) -> dict[str, int] | None:
    """Başlık hücrelerinden item/quantity/weight sütun indekslerini bulur."""
    columns: dict[str, int] = {}
    for index, text in enumerate(cells):
        lowered = text.casefold()
        for name, words in EQUIPMENT_HEADERS.items():
            if name not in columns and any(word in lowered for word in words):
                columns[name] = index
                break
    return columns if "item" in columns and "quantity" in columns else None


def _equipment_rows(rows: list[list[str]], columns: dict[str, int]) -> tuple[list[dict[str, str]], str]:
    """Başlıktan sonraki satırları ekipman kalemlerine ve toplam ağırlığa ayırır."""
    def cell(row: list[str], name: str) -> str:
        index = columns.get(name, -1)
        return one_line(row[index]) if 0 <= index < len(row) else ""

    items, total = [], ""
    for row in rows:
        filled = [one_line(text) for text in row if one_line(text)]
        if not filled:
            continue
        item, quantity, weight = cell(row, "item"), cell(row, "quantity"), cell(row, "weight")
        is_total = any(_TOTAL_RE.match(text) for text in filled)
        if is_total or (not item and not quantity and weight):
            values = [text for text in filled if not _TOTAL_RE.match(text)]
            total = values[-1] if values else total
            continue
        if item:
            items.append({"item": item, "quantity": quantity, "weight": weight})
    return items, total


def parse_equipment(soup: BeautifulSoup) -> tuple[list[dict[str, str]], str]:
    """Ekipman tablosunu bulur, TÜM satırları ve toplam ağırlığı döner; tabloyu soup'tan çıkarır."""
    for table in soup.find_all("table"):
        rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
        texts = [[clean_text(c.get_text()) for c in row.find_all(["th", "td"], recursive=False)] for row in rows]
        for header_index, header in enumerate(texts[:3]):
            columns = _equipment_columns(header)
            if not columns:
                continue
            items, total = _equipment_rows(texts[header_index + 1:], columns)
            if items:
                table.decompose()
                return items, total
    return [], ""


def empty_details() -> dict[str, Any]:
    """Ayrıştırma ara biçimi: bölüm -> alan -> ham değer."""
    return {"waybill": {}, "dr_form": {}, "meta": {}, "raw": {}, "equipment": []}


def _phone_target(last_field: str) -> tuple[str, str] | None:
    """Genel 'Phone/Tel' etiketini, hemen önceki alana göre doğru hedefe yönlendirir."""
    if last_field == "tour_manager":
        return "waybill", "tour_manager_phone"
    if last_field == "delivery_location":
        return "waybill", "delivery_phone"
    if last_field == "return_location":
        return "raw", "return_phone"
    return None  # bağlamı belirsiz telefon: yanlış alana yazmaktansa atlanır


def map_pairs(pairs: Iterable[tuple[str, str]]) -> dict[str, Any]:
    """Etiket/değer çiftlerini şema alanlarına eşler; tanınmayanlar raw'a gider."""
    details = empty_details()
    last_field = ""
    for label, value in pairs:
        key, value = norm_label(label), clean_text(value)
        if not key or not value:
            continue
        is_phone = key in PHONE_LABELS
        target = _phone_target(last_field) if is_phone else LABEL_MAP.get(key)
        if target is None and is_phone:
            continue
        if target is None:
            raw_label = one_line(label).rstrip(":").strip()
            if len(details["raw"]) < MAX_RAW_ENTRIES and len(raw_label) <= 45 and len(value) <= 400:
                details["raw"].setdefault(raw_label, value)
            continue
        section, name = target
        details[section].setdefault(name, value)
        last_field = name
    return details


def parse_detail_html(html: str) -> dict[str, Any]:
    """Tek bir HTML parçasını (modal, WayBill, D&R Form) ara biçime ayrıştırır."""
    soup = prepare_soup(html)
    equipment, total = parse_equipment(soup)
    details = map_pairs(_pairs_from_soup(soup))
    details["equipment"] = equipment
    if total:
        details["dr_form"]["total_weight"] = total
    return details


def merge_details(parts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Birden çok ayrıştırma sonucunu birleştirir; dolu olan ilk değer kazanır."""
    merged = empty_details()
    for part in parts:
        for section in ("waybill", "dr_form", "meta", "raw"):
            for name, value in part.get(section, {}).items():
                if value and not merged[section].get(name):
                    merged[section][name] = value
        if part.get("equipment") and not merged["equipment"]:
            merged["equipment"] = part["equipment"]
    return merged


def _finalize_waybill(source: dict[str, str], raw: dict[str, str]) -> dict[str, str]:
    """WayBill alanlarını şemaya göre düzenler: ad/telefon ve adres/telefon ayrımı, tarihler."""
    waybill = {name: one_line(source.get(name, "")) for name in WAYBILL_FIELDS}
    manager_name, manager_phone = split_phone(source.get("tour_manager", ""))
    waybill["tour_manager_name"] = one_line(manager_name).strip(" ,;-")
    waybill["tour_manager_phone"] = waybill["tour_manager_phone"] or manager_phone
    delivery, delivery_phone = split_phone(source.get("delivery_location", ""))
    waybill["delivery_location"] = delivery
    waybill["delivery_phone"] = waybill["delivery_phone"] or delivery_phone
    returning, return_phone = split_phone(source.get("return_location", ""))
    waybill["return_location"] = returning
    if return_phone:
        raw.setdefault("return_phone", return_phone)
    waybill["tour_start"] = normalize_date(source.get("tour_start", ""))
    waybill["tour_end"] = normalize_date(source.get("tour_end", ""))
    return waybill


def _cross_fill(waybill: dict[str, str], dr_form: dict[str, Any]) -> None:
    """Bir belgede eksik kalan eşdeğer alanı diğer belgeden tamamlar."""
    twins = (("qv_id", "qv_booking_id"), ("client", "customer"),
             ("tour_reference", "reference"), ("tour_start", "service_start_date"))
    for waybill_name, dr_name in twins:
        waybill[waybill_name] = waybill[waybill_name] or dr_form[dr_name]
        dr_form[dr_name] = dr_form[dr_name] or waybill[waybill_name]


def finalize_details(merged: dict[str, Any]) -> dict[str, Any]:
    """Ara biçimi data.json şemasındaki waybill/dr_form/raw yapısına çevirir."""
    raw = {str(key): str(value) for key, value in merged.get("raw", {}).items()}
    waybill = _finalize_waybill(merged.get("waybill", {}), raw)
    source = merged.get("dr_form", {})
    dr_form: dict[str, Any] = {name: one_line(source.get(name, "")) for name in DR_FIELDS}
    dr_form["service_start_date"] = normalize_date(dr_form["service_start_date"])
    _cross_fill(waybill, dr_form)
    dr_form["qr_content"] = dr_form["qv_booking_id"]
    dr_form["equipment"] = [dict(row) for row in merged.get("equipment", [])]
    dr_form["total_weight"] = one_line(source.get("total_weight", ""))
    meta = {name: one_line(value) for name, value in merged.get("meta", {}).items()}
    return {"waybill": waybill, "dr_form": dr_form, "meta": meta, "raw": raw}


def parse_detail_documents(html_parts: Iterable[str]) -> dict[str, Any]:
    """Bir etkinliğe ait tüm HTML parçalarını ayrıştırıp şemaya uygun detay döner."""
    return finalize_details(merge_details(parse_detail_html(html) for html in html_parts))


# --- Quietvox'un gerçek uçları (canlı sitede doğrulanan yapı) -----------------
# Takvim, etkinlikleri JSON olarak `getContent.html?type=...&start=...&end=...`
# adresinden alır; WayBill ve D&R Formu ise sipariş kimliğiyle ayrı HTML uçlarından
# gelir. Bu yol hiçbir şeye tıklamaz, bu yüzden hem hızlı hem de güvenlidir.

def feed_url_for(calendar_url: str, kind: str, today: date, days: int) -> str:
    """Takvim sayfasının adresinden etkinlik feed adresini üretir (pencere ±1 gün paylı)."""
    start, end = window_bounds(today, days)
    zone = ZoneInfo(TIMEZONE_NAME)
    low = datetime.combine(start - timedelta(days=1), datetime.min.time(), zone)
    high = datetime.combine(end + timedelta(days=2), datetime.min.time(), zone)
    base = calendar_url.split("#")[0].split("?")[0]
    query = urllib.parse.urlencode(
        {"type": kind, "start": int(low.timestamp()), "end": int(high.timestamp())})
    return f"{urllib.parse.urljoin(base, FEED_FILE)}?{query}"


def _html_lines(html: Any) -> str:
    """HTML parçasını metne çevirir; <br> satır sonu sayılır."""
    with_breaks = re.sub(r"<br\s*/?>", "\n", str(html or ""), flags=re.I)
    return clean_text(BeautifulSoup(with_breaks, "html.parser").get_text())


def parse_feed_items(payload: Any, today: date, days: int) -> list[dict[str, str]] | None:
    """Feed JSON'unu pencere içindeki {id,date,time,title} listesine çevirir; biçim tanınmazsa None."""
    if not isinstance(payload, list):
        return None
    items: list[dict[str, str]] = []
    for entry in payload:
        if not isinstance(entry, dict) or entry.get("id") in (None, ""):
            continue
        start = _feed_start(entry.get("start"))
        day = valid_iso(start[:10])
        if not in_window(day, today, days):
            continue
        time_text = "" if entry.get("allDay", True) else extract_time(start[10:])
        title = " · ".join(_html_lines(entry.get("title")).split("\n"))
        items.append({"id": str(entry["id"]), "date": day, "time": time_text, "title": title})
    return items


def _browser_soup(html: str) -> BeautifulSoup:
    """Belgeyi tarayıcının yaptığı gibi ayrıştırır (kapanmamış hücreler vb. için html5lib)."""
    try:
        return BeautifulSoup(html, "html5lib")
    except Exception:  # html5lib kurulu değilse yerleşik ayrıştırıcıya düş
        return BeautifulSoup(html, "html.parser")


def _cell_text(cell: Any) -> str:
    return _html_lines(cell.decode_contents())


def _store_direct(details: dict[str, Any], label: str, value: str) -> None:
    """Gerçek sitedeki etiketi ara biçime yazar; dolu olan ilk değer korunur."""
    key = norm_label(label)
    if not key or not value:
        return
    target = DIRECT_LABELS.get(key) or LABEL_MAP.get(key)
    if target is None:
        if len(details["raw"]) < MAX_RAW_ENTRIES and len(value) <= 400:
            details["raw"].setdefault(one_line(label).rstrip(":").strip(), value)
        return
    section, name = target
    details[section].setdefault(name, value)


def parse_waybill_tables(html: str) -> dict[str, Any]:
    """WayBill: her tabloda başlık satırı (th) ve hemen altında değer satırı (td) bulunur."""
    details = empty_details()
    for table in _browser_soup(html).find_all("table"):
        rows = table.find_all("tr")
        for head, body in zip(rows, rows[1:]):
            labels = head.find_all("th", recursive=False)
            cells = body.find_all("td", recursive=False)
            if not labels or len(labels) != len(cells):
                continue
            for label, cell in zip(labels, cells):
                _store_direct(details, label.get_text(), _cell_text(cell))
    return details


def _dr_equipment(table: Any) -> tuple[list[dict[str, str]], str, str]:
    """D&R ekipman tablosu: (kalemler, teslim ağırlığı, iade ağırlığı)."""
    items: list[dict[str, str]] = []
    delivered_weight = returned_weight = ""
    # Yalnızca bu tablonun kendi satırları: "Bag ID" hücrelerindeki iç tablolar kalem değildir
    own_rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
    for row in own_rows[1:]:
        cells = [_cell_text(cell) for cell in row.find_all(["td", "th"], recursive=False)]
        if len(cells) < 2:
            continue
        name = one_line(cells[0]).lstrip("#").strip()
        returned = cells[3] if len(cells) > 3 else ""
        if norm_label(name).startswith("weight"):
            delivered_weight, returned_weight = one_line(cells[1]), one_line(returned)
        elif name and norm_label(name) not in DIRECT_SKIPPED_ROWS:
            items.append({"item": name, "quantity": one_line(cells[1]), "weight": "",
                          "return_quantity": one_line(returned)})
    return items, delivered_weight, returned_weight


def parse_dr_form_tables(html: str) -> dict[str, Any]:
    """D&R Formu: etiket (th) ve yanındaki değer (td) çiftleri + ekipman tablosu."""
    details = empty_details()
    soup = _browser_soup(html)
    equipment_table = soup.find("table", id=DR_EQUIPMENT_TABLE_ID)
    for label in soup.find_all("th"):
        if equipment_table is not None and label.find_parent("table") is equipment_table:
            continue
        cell = label.find_next_sibling()
        if cell is not None and cell.name == "td":
            _store_direct(details, label.get_text(), _cell_text(cell))
    if equipment_table is not None:
        items, delivered_weight, returned_weight = _dr_equipment(equipment_table)
        details["equipment"] = items
        if delivered_weight:
            details["dr_form"]["total_weight"] = delivered_weight
        if returned_weight:
            details["raw"]["Return weight"] = returned_weight
    return details


def parse_direct_documents(dr_html: str, waybill_html: str) -> dict[str, Any]:
    """Gerçek D&R ve WayBill HTML'lerini şemaya çevirir (D&R önceliklidir: adresler oradadır)."""
    return finalize_details(merge_details(
        [parse_dr_form_tables(dr_html), parse_waybill_tables(waybill_html)]))


def has_direct_content(details: dict[str, Any] | None) -> bool:
    """Detay gerçekten okundu mu? (oturum düşerse uçlar giriş sayfası döndürür)"""
    if not details:
        return False
    return bool(details["dr_form"]["customer"] or details["waybill"]["client"]
                or details["dr_form"]["equipment"])


def separate_repeated_ids(orders: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aynı sipariş farklı günlerde (parçalı teslimat) görünüyorsa kayıtlar ayrı tutulur."""
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for order in sort_orders(orders):
        key = order["id"]
        if key in seen:
            order = {**order, "id": f"{key}_{order['date']}"}
        seen.add(order["id"])
        result.append(order)
    return result


def _feed_start(value: Any) -> str:
    """Feed 'start' değerini metne çevirir (epoch ms/sn sayıları dahil)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, ZoneInfo(TIMEZONE_NAME)).strftime("%Y-%m-%dT%H:%M")
    return str(value or "")


def _feed_event(item: Any) -> dict[str, str] | None:
    """Tek bir feed öğesini {id,title,start,url} biçimine çevirir; uygun değilse None."""
    if not isinstance(item, dict):
        return None
    start = _feed_start(item.get("start") or item.get("date") or item.get("start_date"))
    if not start or ("title" not in item and "id" not in item):
        return None
    return {
        "id": str(item.get("id") if item.get("id") is not None else ""),
        "title": one_line(item.get("title") or item.get("name") or ""),
        "start": start,
        "url": str(item.get("url") or ""),
    }


def extract_feed_events(payload: Any, depth: int = 0) -> list[dict[str, str]]:
    """JSON yanıtında takvim etkinlik dizisi arar (start + title/id alanlı sözlükler)."""
    if depth > 3:
        return []
    if isinstance(payload, list):
        events = [event for event in map(_feed_event, payload) if event]
        return events if events and len(events) >= len(payload) / 2 else []
    if isinstance(payload, dict):
        found: list[dict[str, str]] = []
        for value in payload.values():
            if isinstance(value, (list, dict)):
                found.extend(extract_feed_events(value, depth + 1))
        return found
    return []


def feed_date_time(event: dict[str, str] | None) -> tuple[str, str]:
    """Feed etkinliğinden (tarih, saat) çıkarır; gece yarısı tüm gün sayılır."""
    if not event:
        return "", ""
    start = event.get("start", "")
    event_date = valid_iso(start[:10]) or valid_iso(normalize_date(start))
    event_time = extract_time(start[10:]) if len(start) > 10 else ""
    return event_date, "" if event_time == "00:00" else event_time


def match_feed_event(dom_event: dict[str, Any], feed: list[dict[str, str]]) -> dict[str, str] | None:
    """DOM etkinliğini feed etkinliğiyle eşler (önce id, sonra başlık; belirsizse None)."""
    dom_id = str(dom_event.get("id") or "")
    if dom_id:
        by_id = [event for event in feed if event["id"] == dom_id]
        if len({event["start"] for event in by_id}) == 1:
            return by_id[0]
    text = one_line(dom_event.get("text", "")).casefold()
    title = one_line(dom_event.get("title", "")).casefold()
    matches = [
        event for event in feed
        if event["title"] and (event["title"].casefold() == title or event["title"].casefold() in text)
    ]
    wanted = valid_iso(dom_event.get("date", ""))
    if wanted:
        matches = [event for event in matches if event["start"][:10] == wanted]
    return matches[0] if len({event["start"] for event in matches}) == 1 else None


def _is_real_href(href: str) -> bool:
    href = (href or "").strip().lower()
    return bool(href) and not href.startswith(("#", "javascript:", "mailto:", "tel:"))


def _link_matches(spec: CalendarSpec, haystack: str) -> bool:
    return (spec.kind in haystack and "calendar" in haystack
            and (("cruise" in haystack) == spec.cruise))


def match_calendar_links(links: Iterable[dict[str, str]], specs: Iterable[CalendarSpec] = CALENDARS) -> dict[str, str]:
    """Menü bağlantılarından takvim URL'lerini bulur: önce link metni, sonra href."""
    usable = [link for link in links if _is_real_href(link.get("href", ""))]
    found: dict[str, str] = {}
    for spec in specs:
        for attribute in ("text", "href"):
            for link in usable:
                haystack = re.sub(r"\s+", " ", str(link.get(attribute, ""))).casefold()
                if _link_matches(spec, haystack):
                    found.setdefault(spec.key, link.get("abs") or link["href"])
    return found


def candidate_urls(spec: CalendarSpec, settings: Settings, discovered: Mapping[str, str]) -> list[str]:
    """Bir takvim için denenecek URL listesi: ortam değişkeni > menü keşfi > tahminler."""
    base = settings.base_url + "/"
    override = settings.url_overrides.get(spec.key, "")
    if override:
        return [urllib.parse.urljoin(base, override)]
    urls = [urllib.parse.urljoin(base, path) for path in spec.paths]
    if spec.key in discovered:
        urls.insert(0, urllib.parse.urljoin(base, discovered[spec.key]))
    return list(dict.fromkeys(urls))


# ============================================================================
# 3) SİPARİŞ KURMA, TEKİLLEŞTİRME, BELGE BİRLEŞTİRME
# ============================================================================


def window_bounds(today: date, days: int) -> tuple[date, date]:
    """Bugün dahil `days` günlük pencerenin ilk ve son günü."""
    return today, today + timedelta(days=days - 1)


def in_window(date_text: Any, today: date, days: int) -> bool:
    """Tarih (YYYY-AA-GG) pencere içinde mi?"""
    iso = valid_iso(date_text)
    if not iso:
        return False
    start, end = window_bounds(today, days)
    return start <= date.fromisoformat(iso) <= end


def fallback_id(kind: str, date_text: str, title: str) -> str:
    """QV Booking ID yoksa kullanılan kararlı yedek anahtar (tür + tarih + başlık)."""
    digest = hashlib.sha1(one_line(title).casefold().encode("utf-8")).hexdigest()[:8]
    return f"{kind}-{date_text}-{digest}"


def _first_line(text: str) -> str:
    return clean_text(text).split("\n")[0] if clean_text(text) else ""


def _strip_time_prefix(title: str) -> str:
    """Başlığın başındaki saat metnini ('09:30', '9:30a', '2p - 3p') temizler."""
    return _TIME_PREFIX_RE.sub("", one_line(title)).strip() or one_line(title)


def _order_date(kind: str, event_date: str, details: dict[str, Any]) -> str:
    """Etkinlik tarihi: takvim hücresi/feed öncelikli, yoksa modal içi tarih alanları."""
    waybill, dr_form, meta = details["waybill"], details["dr_form"], details["meta"]
    if kind == "delivery":
        candidates = (event_date, meta.get("delivery_date"), meta.get("date"),
                      waybill["tour_start"], dr_form["service_start_date"])
    else:
        candidates = (event_date, meta.get("return_date"), meta.get("date"), waybill["tour_end"])
    for candidate in candidates:
        iso = valid_iso(normalize_date(candidate or ""))
        if iso:
            return iso
    return ""


def build_order(spec: CalendarSpec, event_date: str, event_time: str, title: str,
                details: dict[str, Any] | None) -> dict[str, Any] | None:
    """Takvim etkinliği + modal detayından data.json sipariş kaydı üretir (tarih yoksa None)."""
    details = details or finalize_details(empty_details())
    waybill, dr_form, meta = details["waybill"], details["dr_form"], details["meta"]
    order_date = _order_date(spec.kind, event_date, details)
    if not order_date:
        return None
    title = _strip_time_prefix(title)
    location = waybill["delivery_location"] if spec.kind == "delivery" else waybill["return_location"]
    time_text = event_time or extract_time(meta.get(f"{spec.kind}_time", "")) or extract_time(meta.get("time", ""))
    booking_id = dr_form["qv_booking_id"] or waybill["qv_id"]
    return {
        "id": booking_id or fallback_id(spec.kind, order_date, title),
        "type": spec.kind,
        "date": order_date,
        "time": time_text,
        "cruise": spec.cruise,
        "source_calendar": spec.name,
        "title": title,
        "location_name": _first_line(location) or title,
        "waybill": waybill,
        "dr_form": dr_form,
        "raw": details["raw"],
    }


def _richness(order: dict[str, Any]) -> int:
    """Kayıttaki dolu alan sayısı; aynı ID'li kayıtlardan zengin olanı seçmek için."""
    values = list(order.get("waybill", {}).values()) + list(order.get("dr_form", {}).values())
    return sum(1 for value in values if value)


def dedupe_orders(orders: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tek bir liste içinde ID bazında tekilleştirir; daha dolu kayıt tercih edilir."""
    unique: dict[str, dict[str, Any]] = {}
    for order in orders:
        key = order.get("id") or fallback_id(order.get("type", ""), order.get("date", ""), order.get("title", ""))
        if key not in unique or _richness(order) > _richness(unique[key]):
            unique[key] = order
    return list(unique.values())


def sort_orders(orders: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tarih, saat ve id'ye göre sıralar."""
    return sorted(orders, key=lambda order: (order.get("date", ""), order.get("time", ""), order.get("id", "")))


def utc_stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty_document(settings: Settings, now: datetime) -> dict[str, Any]:
    """Boş (siparişsiz) data.json iskeleti."""
    return {
        "schema_version": SCHEMA_VERSION,
        "demo": False,
        "status": STATUS_OK,
        "status_message": "",
        "generated_at": utc_stamp(now),
        "timezone": TIMEZONE_NAME,
        "window_days": settings.window_days,
        "deliveries": [],
        "returns": [],
    }


def _preserved_orders(previous: dict[str, Any] | None, spec: CalendarSpec, settings: Settings) -> list[dict[str, Any]]:
    """Okunamayan takvimin önceki (demo olmayan, hâlâ pencere içindeki) kayıtları."""
    if not previous or previous.get("demo"):
        return []
    kept = []
    for order in previous.get(LIST_KEY_BY_KIND[spec.kind], []) or []:
        same_calendar = isinstance(order, dict) and order.get("source_calendar") == spec.name
        if same_calendar and in_window(order.get("date", ""), settings.today, settings.window_days):
            kept.append(order)
    return kept


def _status_for(results: Mapping[str, list | None], detail_errors: int) -> tuple[str, str]:
    """Takvim sonuçlarından status ve Türkçe status_message üretir."""
    failed = [spec.name for spec in CALENDARS if results.get(spec.key) is None]
    notes = []
    if failed:
        notes.append("Şu takvimler okunamadı, eski kayıtlar korundu: " + ", ".join(failed) + ".")
    if detail_errors:
        notes.append(f"{detail_errors} etkinliğin detayı okunamadı.")
    return (STATUS_PARTIAL if notes else STATUS_OK), " ".join(notes)


def assemble_document(previous: dict[str, Any] | None, results: Mapping[str, list | None],
                      detail_errors: int, settings: Settings, now: datetime) -> dict[str, Any]:
    """Takvim sonuçlarını tek belgeye birleştirir; başarısız takvimin eski kayıtlarını korur."""
    document = empty_document(settings, now)
    for spec in CALENDARS:
        orders = results.get(spec.key)
        if orders is None:
            orders = _preserved_orders(previous, spec, settings)
        document[LIST_KEY_BY_KIND[spec.kind]].extend(orders)
    for list_key in LIST_KEY_BY_KIND.values():
        document[list_key] = sort_orders(dedupe_orders(document[list_key]))
    document["status"], document["status_message"] = _status_for(results, detail_errors)
    return document


def status_only_document(previous: dict[str, Any] | None, status: str, message: str,
                         settings: Settings, now: datetime) -> dict[str, Any]:
    """Mevcut siparişleri KORUYARAK yalnızca status ve status_message alanlarını günceller."""
    document = copy.deepcopy(previous) if previous else empty_document(settings, now)
    document["status"] = status
    document["status_message"] = message
    return document


# ============================================================================
# 4) DOSYA YAZIMI VE BİLDİRİMLER
# ============================================================================


def load_previous(path: Path) -> dict[str, Any] | None:
    """Önceki data.json'ı okur; yoksa ya da bozuksa None."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("Önceki %s okunamadı (%s); yok sayılıyor.", path.name, type(exc).__name__)
        return None
    return data if isinstance(data, dict) else None


def write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    """JSON'u UTF-8 olarak geçici dosyaya yazar, sonra atomik biçimde yerine koyar."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def content_signature(document: dict[str, Any] | None) -> str:
    """generated_at dışındaki anlamlı içeriğin karşılaştırma imzası."""
    if not document:
        return ""
    keys = ("schema_version", "demo", "status", "status_message", "timezone", "window_days", "deliveries", "returns")
    return json.dumps({key: document.get(key) for key in keys}, sort_keys=True, ensure_ascii=False)


def _age_seconds(previous: dict[str, Any], now: datetime) -> float | None:
    """Önceki generated_at'in yaşı (saniye); okunamazsa None."""
    try:
        stamp = datetime.fromisoformat(str(previous.get("generated_at", "")).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (now - stamp).total_seconds()


def should_write(previous: dict[str, Any] | None, document: dict[str, Any], now: datetime) -> bool:
    """İçerik + status aynıysa ve önceki dosya 2 saatten yeniyse yazma (gereksiz commit önlenir)."""
    if not previous or content_signature(previous) != content_signature(document):
        return True
    age = _age_seconds(previous, now)
    return age is None or age < 0 or age >= FRESHNESS_SECONDS


def write_if_changed(settings: Settings, previous: dict[str, Any] | None,
                     document: dict[str, Any], now: datetime) -> bool:
    """Gerekliyse generated_at'i güncelleyip yazar; yazıldıysa True."""
    if not should_write(previous, document, now):
        log.info("İçerik değişmedi ve dosya taze; %s'a dokunulmadı.", settings.output.name)
        return False
    write_json_atomic(settings.output, {**document, "generated_at": utc_stamp(now)})
    log.info("%s yazıldı: %d teslimat, %d iade, durum=%s.", settings.output.name,
             len(document["deliveries"]), len(document["returns"]), document["status"])
    return True


def write_status_only(settings: Settings, previous: dict[str, Any] | None, status: str,
                      message: str, now: datetime) -> None:
    """Siparişlere dokunmadan durum alanlarını yazar (değişiklik yoksa yazmaz)."""
    document = status_only_document(previous, status, message, settings, now)
    if previous and content_signature(previous) == content_signature(document):
        log.info("Durum zaten '%s'; %s'a dokunulmadı.", status, settings.output.name)
        return
    write_json_atomic(settings.output, document)
    log.info("%s güncellendi: durum=%s.", settings.output.name, status)


def send_ntfy(settings: Settings, message: str, priority: str = "default") -> bool:
    """ntfy'ye UTF-8 gövdeli bildirim yollar; hata scraper'ı çökertmez. Başlıklar ASCII'dir."""
    if not settings.ntfy_topic:
        return False
    url = settings.ntfy_url.rstrip("/") + "/" + urllib.parse.quote(settings.ntfy_topic, safe="")
    headers = {"Title": "Quietvox", "Priority": priority, "Content-Type": "text/plain; charset=utf-8"}
    request = urllib.request.Request(url, data=message.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=NTFY_TIMEOUT_SECONDS) as response:
            response.read()
    except Exception as exc:  # bildirim hatası asla ölümcül değildir
        log.warning("ntfy bildirimi gönderilemedi (%s).", type(exc).__name__)
        return False
    log.info("ntfy bildirimi gönderildi.")
    return True


def _names_summary(orders: list[dict[str, Any]]) -> str:
    """Parantez içi konum adları: tekrarsız, en fazla 4 ad + '…'."""
    names = list(dict.fromkeys(
        one_line(order.get("location_name") or order.get("title") or "") for order in orders))
    names = [name for name in names if name]
    if not names:
        return ""
    shown = ", ".join(names[:MAX_NAMES_IN_REMINDER])
    return f" ({shown}{', …' if len(names) > MAX_NAMES_IN_REMINDER else ''})"


def build_reminder_text(deliveries: list[dict[str, Any]], returns: list[dict[str, Any]], tomorrow: date) -> str:
    """Yarının teslimat ve iadeleri için Türkçe özet metni."""
    iso = tomorrow.isoformat()
    due_deliveries = [order for order in deliveries if order.get("date") == iso]
    due_returns = [order for order in returns if order.get("date") == iso]
    delivery_part = f"{len(due_deliveries)} Teslimat{_names_summary(due_deliveries)}"
    return_part = f"{len(due_returns)} İade Alımı{_names_summary(due_returns)}"
    if due_deliveries and due_returns:
        return f"📦 Yarın {delivery_part} ve 🔄 {return_part} var."
    if due_deliveries:
        return f"📦 Yarın {delivery_part} var."
    if due_returns:
        return f"🔄 Yarın {return_part} var."
    return "Yarın teslimat veya iade yok."


def maybe_send_reminder(settings: Settings, document: dict[str, Any]) -> bool:
    """NTFY_TOPIC tanımlı ve SEND_REMINDER=1 ise yarının özetini gönderir."""
    if not (settings.ntfy_topic and settings.send_reminder):
        return False
    tomorrow = settings.today + timedelta(days=1)
    return send_ntfy(settings, build_reminder_text(document["deliveries"], document["returns"], tomorrow))


# ============================================================================
# 5) PLAYWRIGHT KATMANI
# ============================================================================


class CalendarNotFound(Exception):
    """URL bir takvim sayfası değil (404, giriş sayfası ya da tanınmayan yapı)."""


class DetailError(Exception):
    """Etkinlik detayı (modal/popup) açılamadı."""


# Takvimdeki etkinlikleri toplar; cfg.mark verilirse eşleşen öğeyi tıklama için işaretler.
JS_COLLECT_EVENTS = r"""
(cfg) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const query = (selectors) => {
    const out = [];
    for (const sel of selectors) {
      let found = [];
      try { found = Array.from(document.querySelectorAll(sel)); } catch (e) { continue; }
      for (const el of found) if (!out.includes(el)) out.push(el);
    }
    return out.filter((el) => !out.some((other) => other !== el && other.contains(el)));
  };
  const isoOf = (el) => {
    const m = /^\d{4}-\d{2}-\d{2}/.exec((el && el.getAttribute('data-date')) || '');
    return m ? m[0] : '';
  };
  const cells = Array.from(document.querySelectorAll(cfg.dayCell))
    .map((c) => ({ d: isoOf(c), r: c.getBoundingClientRect() }))
    .filter((c) => c.d && c.r.width > 0 && c.r.height > 0);
  const dateOf = (el) => {
    const own = isoOf(el.closest('[data-date]'));
    if (own) return own;
    let row = el.closest('tr');
    while (row) {  // liste görünümü: tarih başlığı önceki satırdadır
      if (isoOf(row)) return isoOf(row);
      row = row.previousElementSibling;
    }
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return '';
    const x = r.left + Math.min(6, r.width / 2), y = r.top + r.height / 2;
    let best = null;  // geometrik eşleme: etkinliğin sol kenarını içeren en küçük gün hücresi
    for (const c of cells) {
      if (x < c.r.left || x >= c.r.right || y < c.r.top || y >= c.r.bottom) continue;
      if (!best || c.r.width * c.r.height < best.r.width * best.r.height) best = c;
    }
    return best ? best.d : '';
  };
  let els = query(cfg.primary);
  if (!els.length) {
    els = query(cfg.fallback).filter((el) => {
      const text = norm(el.textContent);
      return text.length > 1 && !/^\d{1,2}$/.test(text) && !/day-?number|fc-more|fc-button/i.test(String(el.className));
    });
  }
  document.querySelectorAll('[data-qv-target]').forEach((el) => el.removeAttribute('data-qv-target'));
  const counts = {};
  const events = els.map((el) => {
    const timeEl = el.querySelector(cfg.time), titleEl = el.querySelector(cfg.title);
    const text = norm(el.textContent), time = norm(timeEl && timeEl.textContent), date = dateOf(el);
    const anchor = el.matches('a[href]') ? el : el.querySelector('a[href]');
    const sig = date + '|' + text;
    const occ = counts[sig] = (counts[sig] || 0) + 1;
    if (cfg.mark && cfg.mark.text === text && cfg.mark.date === date && cfg.mark.occ === occ) {
      el.setAttribute('data-qv-target', '1');
    }
    return {
      text, time, date, occ,
      title: norm(titleEl && titleEl.textContent) || text,
      href: anchor ? (anchor.getAttribute('href') || '') : '',
      abs: anchor ? (anchor.href || '') : '',
      target: anchor ? (anchor.getAttribute('target') || '') : '',
      id: el.getAttribute('data-id') || el.getAttribute('data-event-id') || el.getAttribute('data-eventid') || '',
    };
  });
  const dates = cells.map((c) => c.d).sort();
  return {
    events,
    hasRoot: !!document.querySelector(cfg.root),
    maxDate: dates.length ? dates[dates.length - 1] : '',
  };
}
"""

# FullCalendar v3 (jQuery) bellekteki etkinlikleri verir; yoksa boş dizi.
JS_FULLCALENDAR_CLIENT_EVENTS = r"""
() => {
  try {
    const $ = window.jQuery;
    if (!$ || !$.fn || !$.fn.fullCalendar) return [];
    const out = [];
    $('.fc').each(function () {
      let list = [];
      try { list = $(this).fullCalendar('clientEvents') || []; } catch (e) { return; }
      for (const e of list) {
        out.push({
          id: e.id == null ? '' : String(e.id), title: e.title || '', url: e.url || '',
          start: e.start && e.start.format ? e.start.format() : String(e.start || ''),
        });
      }
    });
    return out;
  } catch (e) { return []; }
}
"""

JS_COLLECT_LINKS = r"""
() => Array.from(document.querySelectorAll('a[href]')).map((a) => ({
  text: (a.textContent || '').replace(/\s+/g, ' ').trim(),
  href: a.getAttribute('href') || '',
  abs: a.href || '',
}))
"""

JS_SUB_LINKS = r"""
(root, selector) => Array.from(root.querySelectorAll(selector)).map((el, index) => ({
  index,
  text: (el.textContent || el.value || '').replace(/\s+/g, ' ').trim(),
  href: el.getAttribute('href') || '',
  abs: el.href || '',
  visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
}))
"""

JS_FORCE_HIDE_MODALS = r"""
(selector) => {
  document.querySelectorAll(selector).forEach((el) => {
    el.classList.remove('show', 'in');
    el.style.display = 'none';
  });
  document.querySelectorAll('.modal-backdrop, .ui-widget-overlay').forEach((el) => el.remove());
  document.body.classList.remove('modal-open');
}
"""


def _without_fragment(url: str) -> str:
    return url.split("#", 1)[0]


async def settle(page: Any, timeout: int = SETTLE_TIMEOUT_MS) -> None:
    """Ağ sakinleşene kadar bekler; zaman aşımı hata sayılmaz."""
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        log.debug("Ağ sakinleşmesi beklenirken zaman aşımı (yok sayıldı).")


async def goto(page: Any, url: str, settings: Settings) -> Any:
    """Sayfaya gider; geçici hatalarda adım bazlı yeniden dener."""
    last_error: Exception | None = None
    for attempt in range(1, GOTO_ATTEMPTS + 1):
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=settings.nav_timeout)
            await settle(page)
            return response
        except Exception as exc:
            last_error = exc
            log.warning("Sayfa açılamadı (deneme %d/%d): %s", attempt, GOTO_ATTEMPTS, urllib.parse.urlsplit(url).path)
            await asyncio.sleep(1)
    raise last_error  # type: ignore[misc]


async def find_first_visible(page: Any, selectors: list[str], limit: int = 5) -> Any:
    """Tüm çerçevelerde, aday seçicilerden görünür ilk öğeyi döner; yoksa None."""
    for frame in page.frames:
        for selector in selectors:
            try:
                locator = frame.locator(selector)
                for index in range(min(await locator.count(), limit)):
                    if await locator.nth(index).is_visible():
                        return locator.nth(index)
            except Exception:
                continue
    return None


async def find_in_scope(scope: Any, selectors: list[str]) -> Any:
    """Verilen kapsam (locator) içinde görünür ilk öğeyi döner; yoksa None."""
    for selector in selectors:
        try:
            locator = scope.locator(selector)
            for index in range(min(await locator.count(), 5)):
                if await locator.nth(index).is_visible():
                    return locator.nth(index)
        except Exception:
            continue
    return None


async def dump_debug(settings: Settings, page: Any, name: str, html: str | None = None) -> None:
    """QV_DEBUG=1 iken HTML + ekran görüntüsü döker (müşteri verisi içerir; varsayılan kapalı)."""
    if not settings.debug:
        return
    try:
        settings.debug_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        content = html if html is not None else await page.content()
        (settings.debug_dir / f"{safe}.html").write_text(content, encoding="utf-8")
        await page.screenshot(path=str(settings.debug_dir / f"{safe}.png"), full_page=True)
    except Exception as exc:
        log.warning("Hata ayıklama dökümü alınamadı (%s).", type(exc).__name__)


# ---- Giriş ----------------------------------------------------------------


async def load_cookies(context: Any, settings: Settings) -> bool:
    """cookies.json varsa oturum çerezlerini yükler."""
    try:
        cookies = json.loads(settings.cookies.read_text(encoding="utf-8"))
        if not isinstance(cookies, list) or not cookies:
            return False
        await context.add_cookies(cookies)
    except FileNotFoundError:
        return False
    except Exception as exc:
        log.warning("cookies.json yüklenemedi (%s); normal giriş yapılacak.", type(exc).__name__)
        return False
    log.info("Kayıtlı çerezler yüklendi.")
    return True


async def save_cookies(context: Any, settings: Settings) -> None:
    """Oturum çerezlerini cookies.json'a kaydeder (içerik asla loglanmaz)."""
    try:
        cookies = await context.cookies()
        settings.cookies.parent.mkdir(parents=True, exist_ok=True)
        settings.cookies.write_text(json.dumps(cookies), encoding="utf-8")
    except Exception as exc:
        log.warning("Çerezler kaydedilemedi (%s).", type(exc).__name__)


async def login_form_visible(page: Any, settings: Settings) -> bool:
    return await find_first_visible(page, settings.selectors["login_password"]) is not None


async def session_is_valid(page: Any, settings: Settings) -> bool:
    """Korumalı bilinen sayfayı açıp oturumun hâlâ geçerli olup olmadığına bakar."""
    url = urllib.parse.urljoin(settings.base_url + "/", KNOWN_PROTECTED_PATH)
    response = await goto(page, url, settings)
    if response is None or response.status >= 400:
        return False
    if "login" in urllib.parse.urlsplit(page.url).path.lower():
        return False
    return not await login_form_visible(page, settings)


async def submit_login_form(page: Any, settings: Settings) -> None:
    """Görünen giriş formunu sezgisel olarak doldurur ve gönderir."""
    password = await find_first_visible(page, settings.selectors["login_password"])
    if password is None:
        return
    form = password.locator("xpath=ancestor::form[1]")
    scope = form if await form.count() else password.locator("xpath=ancestor::body[1]")
    try:
        user = await find_in_scope(scope, settings.selectors["login_user"])
        if user is not None:
            await user.fill(settings.user)
        await password.fill(settings.password)
        submit = await find_in_scope(scope, settings.selectors["login_submit"])
        if submit is not None:
            await submit.click()
        else:
            await password.press("Enter")
    except Exception as exc:
        # Playwright hata metni doldurulan değeri içerebilir; ayrıntı bilerek atılır.
        raise RuntimeError(f"Giriş formu doldurulamadı ({type(exc).__name__})") from None
    await page.wait_for_timeout(300)
    await settle(page, settings.nav_timeout)


async def perform_login(page: Any, settings: Settings) -> bool:
    """Giriş sayfasını açar; form göründükçe (en fazla 3 tur) bilgileri girip onaylar."""
    response = await goto(page, settings.login_url, settings)
    if response is not None and response.status == 401:
        log.error("HTTP Basic kimlik doğrulaması reddedildi (401).")
        return False
    if response is not None and response.status >= 400:
        raise RuntimeError(f"Giriş sayfası HTTP {response.status} döndürdü")
    for round_number in range(1, MAX_LOGIN_ROUNDS + 1):
        if not await login_form_visible(page, settings):
            break
        log.info("Giriş formu dolduruluyor (tur %d/%d).", round_number, MAX_LOGIN_ROUNDS)
        await submit_login_form(page, settings)
    return not await login_form_visible(page, settings)


async def ensure_login(context: Any, page: Any, settings: Settings) -> bool:
    """Çerezlerle oturum geçerliyse girişi atlar; değilse giriş yapar ve çerezleri kaydeder."""
    if await load_cookies(context, settings) and await session_is_valid(page, settings):
        log.info("Kayıtlı oturum geçerli; giriş adımı atlandı.")
        return True
    if not await perform_login(page, settings):
        return False
    log.info("Giriş başarılı.")
    await save_cookies(context, settings)
    return True


# ---- Etkinlik detayı (modal / popup / yeni sayfa) -------------------------


class FeedCollector:
    """Ağ yanıtlarını dinleyip JSON etkinlik beslemelerini toplar."""

    def __init__(self) -> None:
        self.events: list[dict[str, str]] = []
        self._tasks: set[asyncio.Task] = set()

    def on_response(self, response: Any) -> None:
        if "json" not in (response.headers.get("content-type") or "").lower():
            return
        task = asyncio.ensure_future(self._consume(response))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _consume(self, response: Any) -> None:
        try:
            self.events.extend(extract_feed_events(await response.json()))
        except Exception:
            return

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


async def fetch_url_html(context: Any, url: str, settings: Settings) -> str:
    """URL'yi ayrı bir sekmede açıp HTML'ini alır (takvim sayfasının durumu bozulmaz)."""
    extra = await context.new_page()
    try:
        await extra.goto(url, wait_until="domcontentloaded", timeout=settings.nav_timeout)
        await settle(extra)
        return await extra.content()
    finally:
        await extra.close()


async def surface_html(surface: Any) -> str:
    """Modal öğesinin HTML'i + içindeki iframe'lerin içeriği."""
    html = await surface.evaluate("el => el.outerHTML")
    for handle in await surface.locator("iframe").element_handles():
        frame = await handle.content_frame()
        if frame is not None:
            try:
                html += "\n" + await frame.content()
            except Exception:
                continue
    return html


async def wait_until_stable(surface: Any, page: Any, settings: Settings) -> None:
    """Modal içeriği (AJAX ile dolabilir) dolup sabitlenene kadar bekler."""
    await settle(page)
    previous, deadline = None, asyncio.get_running_loop().time() + settings.modal_timeout / 1000
    while asyncio.get_running_loop().time() < deadline:
        try:
            text = one_line(await surface.inner_text())
        except Exception:
            text = ""
        if text and text == previous:
            return
        previous = text
        await page.wait_for_timeout(250)


async def close_extra_pages(context: Any, keep: list[Any]) -> None:
    for extra in list(context.pages):
        if extra not in keep:
            try:
                await extra.close()
            except Exception:
                continue


async def close_modal(page: Any, settings: Settings) -> None:
    """Açık modalı kapatır: kapat düğmesi -> Escape -> zorla gizleme."""
    selectors = settings.selectors["modal"]
    for attempt in range(3):
        modal = await find_first_visible(page, selectors)
        if modal is None:
            return
        try:
            closer = await find_in_scope(modal, settings.selectors["modal_close"])
            if attempt == 0 and closer is not None:
                await closer.click(timeout=CLICK_TIMEOUT_MS)
            elif attempt <= 1:
                await page.keyboard.press("Escape")
            else:
                await page.evaluate(JS_FORCE_HIDE_MODALS, ", ".join(selectors))
        except Exception:
            log.debug("Modal kapatma denemesi %d başarısız.", attempt + 1)
        await page.wait_for_timeout(350)


class DetailReader:
    """Tek bir etkinliğin detay HTML parçalarını toplar (modal, sekmeler, popup'lar)."""

    def __init__(self, page: Any, settings: Settings) -> None:
        self.page = page
        self.context = page.context
        self.settings = settings
        self.pattern = re.compile("|".join(f"(?:{p})" for p in settings.sub_link_patterns), re.I)

    async def read(self, event: dict[str, Any], collect_config: dict[str, Any]) -> list[str]:
        """Etkinliğin detayını açar ve HTML parçalarını döner."""
        if _is_real_href(event.get("href", "")) and event.get("abs") and not self.settings.prefer_click:
            return await self._read_page(event["abs"])
        if event.get("feed_only"):
            return []
        return await self._read_by_click(event, collect_config)

    async def _read_page(self, url: str) -> list[str]:
        extra = await self.context.new_page()
        try:
            await extra.goto(url, wait_until="domcontentloaded", timeout=self.settings.nav_timeout)
            await settle(extra)
            parts = [await extra.content()]
            parts += await self._sub_documents(extra, extra.locator("body"))
            return parts
        finally:
            await close_extra_pages(self.context, [self.page])

    async def _click_event(self, event: dict[str, Any], collect_config: dict[str, Any]) -> None:
        mark = {"text": event["text"], "date": event["date"], "occ": event["occ"]}
        await self.page.evaluate(JS_COLLECT_EVENTS, {**collect_config, "mark": mark})
        target = self.page.locator('[data-qv-target="1"]').first
        if not await target.count():
            raise DetailError("etkinlik öğesi yeniden bulunamadı")
        try:
            await target.click(timeout=CLICK_TIMEOUT_MS)
        except Exception:
            await target.evaluate("el => el.click()")  # gizli ("+more") öğeler için

    async def _wait_surface(self, pages_before: list[Any], url_before: str) -> tuple[str, Any]:
        """Tıklama sonrası neyin açıldığını bekler: popup, modal ya da aynı sekmede gezinme."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.settings.modal_timeout / 1000
        while loop.time() < deadline:
            popups = [p for p in self.context.pages if p not in pages_before]
            if popups:
                return "popup", popups[0]
            modal = await find_first_visible(self.page, self.settings.selectors["modal"])
            if modal is not None:
                return "modal", modal
            if _without_fragment(self.page.url) != _without_fragment(url_before):
                return "nav", None
            await self.page.wait_for_timeout(150)
        raise DetailError("modal/pencere açılmadı")

    async def _read_by_click(self, event: dict[str, Any], collect_config: dict[str, Any]) -> list[str]:
        pages_before, url_before = list(self.context.pages), self.page.url
        try:
            await self._click_event(event, collect_config)
            kind, surface = await self._wait_surface(pages_before, url_before)
            if kind == "popup":
                await surface.wait_for_load_state("domcontentloaded", timeout=self.settings.nav_timeout)
                await settle(surface)
                return [await surface.content()] + await self._sub_documents(surface, surface.locator("body"))
            if kind == "nav":
                await settle(self.page)
                html = await self.page.content()
                await self.page.go_back(wait_until="domcontentloaded")
                await settle(self.page)
                return [html]
            await wait_until_stable(surface, self.page, self.settings)
            return [await surface_html(surface)] + await self._sub_documents(self.page, surface)
        finally:
            await close_extra_pages(self.context, pages_before)
            await close_modal(self.page, self.settings)

    async def _sub_links(self, scope: Any) -> list[dict[str, Any]]:
        """Kapsam içindeki WayBill / D&R / Print benzeri bağlantı ve düğmeler."""
        selector = ", ".join(self.settings.selectors["sub_link"])
        try:
            links = await scope.evaluate(JS_SUB_LINKS, selector)
        except Exception:
            return []
        chosen, seen = [], set()
        for link in links:
            text = link["text"]
            if not text or len(text) > 60 or not self.pattern.search(text) or text.casefold() in seen:
                continue
            seen.add(text.casefold())
            chosen.append(link)
        return chosen[:MAX_SUB_DOCUMENTS]

    async def _sub_documents(self, owner: Any, scope: Any) -> list[str]:
        """Ayrı sekme/buton/link arkasındaki WayBill ve D&R belgelerini açıp HTML'lerini toplar."""
        parts: list[str] = []
        selector = ", ".join(self.settings.selectors["sub_link"])
        for link in await self._sub_links(scope):
            try:
                same_page = _without_fragment(link["abs"]) == _without_fragment(owner.url)
                if _is_real_href(link["href"]) and link["abs"] and not same_page:
                    parts.append(await fetch_url_html(self.context, link["abs"], self.settings))
                elif link["visible"]:
                    parts.extend(await self._click_sub_link(owner, scope, scope.locator(selector).nth(link["index"])))
            except Exception as exc:
                log.debug("Alt belge açılamadı (%s).", type(exc).__name__)
        return parts

    async def _click_sub_link(self, owner: Any, scope: Any, element: Any) -> list[str]:
        """Sekme/düğmeye tıklar; açılan popup'ın ya da güncellenen modalın HTML'ini döner."""
        pages_before = list(self.context.pages)
        await element.click(timeout=CLICK_TIMEOUT_MS)
        await owner.wait_for_timeout(400)
        await settle(owner)
        popups = [p for p in self.context.pages if p not in pages_before]
        if popups:
            parts = []
            for popup in popups:
                await settle(popup)
                parts.append(await popup.content())
                await popup.close()
            return parts
        return [await surface_html(scope)]


# ---- Takvim ---------------------------------------------------------------


class CalendarScraper:
    """Tek bir takvimi (gerekirse sonraki ay dahil) tarar ve siparişleri üretir."""

    def __init__(self, page: Any, settings: Settings, spec: CalendarSpec) -> None:
        self.page = page
        self.settings = settings
        self.spec = spec
        self.feed = FeedCollector()
        self.reader = DetailReader(page, settings)
        self.seen: set[tuple[str, str, int]] = set()
        self.orders: list[dict[str, Any]] = []
        self.detail_errors = 0
        self.debug_modals = 0
        selectors = settings.selectors
        self.collect_config = {
            "primary": selectors["event_primary"], "fallback": selectors["event_fallback"],
            "time": ", ".join(selectors["event_time"]), "title": ", ".join(selectors["event_title"]),
            "dayCell": ", ".join(selectors["day_cell"]), "root": ", ".join(selectors["calendar_root"]),
            "mark": None,
        }

    async def run(self, url: str) -> list[dict[str, Any]]:
        self.page.on("response", self.feed.on_response)
        try:
            await self._open(url)
            for view in range(MAX_NEXT_CLICKS + 1):
                snapshot = await self._scan_view(view)
                if not self._needs_next(view, snapshot.get("maxDate", "")):
                    break
                if not await self._click_next():
                    log.warning("%s: 'sonraki ay' düğmesi bulunamadı; yalnızca görünen dönem tarandı.", self.spec.name)
                    break
        finally:
            self.page.remove_listener("response", self.feed.on_response)
        return self.orders

    async def _open(self, url: str) -> None:
        response = await goto(self.page, url, self.settings)
        if response is None or response.status >= 400:
            raise CalendarNotFound(f"HTTP {response.status if response else '?'}")
        if await login_form_visible(self.page, self.settings):
            raise CalendarNotFound("giriş sayfasına yönlendirildi")

    def _needs_next(self, view: int, max_visible: str) -> bool:
        """Pencere sonraki aya (ya da görünen dönemin ötesine) taşıyor mu?"""
        if view >= MAX_NEXT_CLICKS:
            return False
        today, end = window_bounds(self.settings.today, self.settings.window_days)
        months_ahead = (end.year - today.year) * 12 + end.month - today.month
        if view < months_ahead:
            return True
        return bool(valid_iso(max_visible)) and max_visible < end.isoformat()

    async def _click_next(self) -> bool:
        button = await find_first_visible(self.page, self.settings.selectors["next_button"])
        if button is None:
            return False
        await button.click(timeout=CLICK_TIMEOUT_MS)
        await self.page.wait_for_timeout(300)
        await settle(self.page)
        return True

    async def _scan_view(self, view: int) -> dict[str, Any]:
        """Görünen dönemdeki etkinlikleri toplar ve pencere içindekilerin detayını okur."""
        snapshot = await self.page.evaluate(JS_COLLECT_EVENTS, self.collect_config)
        await self.feed.drain()
        feed = self.feed.events + await self.page.evaluate(JS_FULLCALENDAR_CLIENT_EVENTS)
        events = snapshot["events"]
        if view == 0 and not snapshot["hasRoot"] and not events and not feed:
            raise CalendarNotFound("takvim yapısı tanınamadı")
        await dump_debug(self.settings, self.page, f"{self.spec.key}_view{view}")
        if not events and feed:
            log.info("%s: DOM'da etkinlik yok; ağ beslemesindeki %d etkinlik kullanılıyor.", self.spec.name, len(feed))
            events = [self._event_from_feed(item, index) for index, item in enumerate(feed)]
        log.info("%s: görünüm %d içinde %d etkinlik bulundu.", self.spec.name, view + 1, len(events))
        for event in events:
            await self._process_event(event, feed)
        return snapshot

    @staticmethod
    def _event_from_feed(item: dict[str, str], index: int) -> dict[str, Any]:
        return {
            "text": item["title"], "title": item["title"], "time": "", "date": item["start"][:10],
            "occ": index + 1, "href": item["url"], "abs": item["url"], "id": item["id"], "feed_only": True,
        }

    async def _process_event(self, event: dict[str, Any], feed: list[dict[str, str]]) -> None:
        """Tek etkinlik: tarih belirle, pencere dışıysa atla, detayını oku, kaydı kur."""
        feed_date, feed_time = feed_date_time(match_feed_event(event, feed))
        event_date = valid_iso(event.get("date", "")) or feed_date
        window = (self.settings.today, self.settings.window_days)
        if event_date and not in_window(event_date, *window):
            return
        key = (event_date, event["text"], event["occ"])
        if key in self.seen:
            return
        self.seen.add(key)
        details = await self._read_details(event)
        event_time = extract_time(event.get("time", "")) or feed_time
        order = build_order(self.spec, event_date, event_time, event.get("title") or event["text"], details)
        if order is None:
            log.warning("%s: tarihi belirlenemeyen bir etkinlik atlandı.", self.spec.name)
        elif in_window(order["date"], *window):
            self.orders.append(order)

    async def _read_details(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Detayı okur; hata olursa sayar ve None döner (diğer etkinliklere devam edilir)."""
        try:
            parts = await self.reader.read(event, self.collect_config)
        except Exception as exc:
            self.detail_errors += 1
            log.warning("%s: bir etkinliğin detayı okunamadı (%s).", self.spec.name, type(exc).__name__)
            return None
        if not parts:
            return None
        if self.debug_modals < MAX_DEBUG_MODALS:
            self.debug_modals += 1
            await dump_debug(self.settings, self.page, f"{self.spec.key}_modal{self.debug_modals}",
                             "\n<!-- parça -->\n".join(parts))
        return parse_detail_documents(parts)


async def collect_links(page: Any) -> list[dict[str, str]]:
    try:
        return await page.evaluate(JS_COLLECT_LINKS)
    except Exception:
        return []


async def discover_calendar_urls(page: Any, settings: Settings) -> dict[str, str]:
    """Menü bağlantılarından takvim URL'lerini keşfeder (gerekirse bilinen sayfayı da açar)."""
    discovered = match_calendar_links(await collect_links(page))
    if len(discovered) < len(CALENDARS):
        try:
            await goto(page, urllib.parse.urljoin(settings.base_url + "/", KNOWN_PROTECTED_PATH), settings)
            discovered = {**match_calendar_links(await collect_links(page)), **discovered}
        except Exception as exc:
            log.debug("Menü keşfi için bilinen sayfa açılamadı (%s).", type(exc).__name__)
    log.info("Menüden keşfedilen takvim sayısı: %d/%d.", len(discovered), len(CALENDARS))
    return discovered


async def _get_text(context: Any, url: str, settings: Settings) -> str:
    """Oturum çerezleriyle (tarayıcı bağlamı üzerinden) GET isteği atar."""
    response = await context.request.get(
        url, timeout=settings.nav_timeout, headers={"X-Requested-With": "XMLHttpRequest"})
    if not response.ok:
        raise DetailError(f"HTTP {response.status}")
    return await response.text()


async def _direct_order(context: Any, settings: Settings, spec: CalendarSpec,
                        item: dict[str, str], gate: asyncio.Semaphore) -> tuple[dict[str, Any] | None, bool]:
    """Tek siparişin D&R ve WayBill belgelerini çekip kayda çevirir; (kayıt, detay okundu mu)."""
    query = "?" + urllib.parse.urlencode({"id": item["id"]})
    details = None
    async with gate:
        try:
            dr_html = await _get_text(context, settings.base_url + DR_FORM_PATH + query, settings)
            waybill_html = await _get_text(context, settings.base_url + WAYBILL_PATH + query, settings)
            details = parse_direct_documents(dr_html, waybill_html)
        except Exception as exc:
            log.warning("%s: bir siparişin belgeleri alınamadı (%s).", spec.name, type(exc).__name__)
    readable = has_direct_content(details)
    order = build_order(spec, item["date"], item["time"], item["title"], details if readable else None)
    if order and not readable:
        order = {**order, "id": item["id"]}
    return order, readable


async def scrape_calendar_direct(page: Any, settings: Settings, spec: CalendarSpec,
                                 url: str) -> tuple[list[dict[str, Any]], int] | None:
    """Takvimi tıklamadan, feed + belge uçlarıyla okur; feed tanınmazsa None döner."""
    feed_url = feed_url_for(url, spec.kind, settings.today, settings.window_days)
    try:
        payload = json.loads(await _get_text(page.context, feed_url, settings))
    except Exception as exc:
        log.debug("%s: feed alınamadı (%s).", spec.name, type(exc).__name__)
        return None
    items = parse_feed_items(payload, settings.today, settings.window_days)
    if items is None:
        return None
    unique = list({(item["id"], item["date"]): item for item in items}.values())
    gate = asyncio.Semaphore(DIRECT_CONCURRENCY)
    results = await asyncio.gather(
        *(_direct_order(page.context, settings, spec, item, gate) for item in unique))
    orders = [order for order, _readable in results if order]
    errors = sum(1 for _order, readable in results if not readable)
    return separate_repeated_ids(orders), errors


async def scrape_calendar(page: Any, settings: Settings, spec: CalendarSpec,
                          urls: list[str]) -> tuple[list[dict[str, Any]] | None, int]:
    """Aday URL'leri sırayla dener; (siparişler | None, detay hata sayısı) döner."""
    for url in urls:
        direct = await scrape_calendar_direct(page, settings, spec, url)
        if direct is not None:
            log.info("%s: doğrudan uçlardan pencere içinde %d kayıt.", spec.name, len(direct[0]))
            return direct
        if not settings.click_fallback:
            log.info("%s: feed okunamadı, tıklama yolu kapalı: %s", spec.name,
                     urllib.parse.urlsplit(url).path)
            continue
        scraper = CalendarScraper(page, settings, spec)
        try:
            orders = await scraper.run(url)
        except CalendarNotFound as exc:
            log.info("%s: aday URL uygun değil (%s): %s", spec.name, exc, urllib.parse.urlsplit(url).path)
            continue
        except Exception as exc:
            log.error("%s: takvim taranırken hata (%s).", spec.name, type(exc).__name__)
            return None, scraper.detail_errors
        log.info("%s: pencere içinde %d kayıt.", spec.name, len(orders))
        return orders, scraper.detail_errors
    log.error("%s: takvim sayfası bulunamadı (%s ile URL verilebilir).", spec.name, spec.env_var)
    return None, 0


async def scrape_all(page: Any, settings: Settings) -> tuple[dict[str, list | None], int]:
    """Dört takvimi sırayla tarar; biri patlarsa diğerlerine devam eder."""
    discovered = await discover_calendar_urls(page, settings)
    results: dict[str, list | None] = {}
    total_errors = 0
    for spec in CALENDARS:
        if spec.key not in discovered:
            discovered = {**match_calendar_links(await collect_links(page)), **discovered}
        urls = candidate_urls(spec, settings, discovered)
        results[spec.key], errors = await scrape_calendar(page, settings, spec, urls)
        total_errors += errors
    return results, total_errors


# ============================================================================
# 6) ORKESTRASYON
# ============================================================================


@dataclass
class Outcome:
    """Tarayıcı oturumunun sonucu."""

    state: str  # "ok" | "login_failed" | "unreachable"
    results: dict[str, list | None] = field(default_factory=dict)
    detail_errors: int = 0
    message: str = ""


class SecretFilter(logging.Filter):
    """Log kayıtlarından kullanıcı adı/şifre gibi sırları maskeleyen filtre."""

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self.secrets = sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for secret in self.secrets:
            message = message.replace(secret, "***")
        record.msg, record.args = message, None
        return True


def configure_logging(settings: Settings) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    handler.addFilter(SecretFilter([settings.password, settings.http_pass, settings.ntfy_topic]))
    logging.basicConfig(level=logging.DEBUG if settings.debug else logging.INFO, handlers=[handler], force=True)


async def new_context(browser: Any, settings: Settings) -> Any:
    """HTTP Basic kimlik bilgileriyle tarayıcı bağlamı açar."""
    context = await browser.new_context(
        http_credentials={"username": settings.http_user, "password": settings.http_pass},
        locale="en-GB", timezone_id=TIMEZONE_NAME, viewport={"width": 1440, "height": 1000},
    )
    context.set_default_timeout(settings.nav_timeout)
    await context.add_init_script("window.print = () => {};")  # Print düğmeleri tarayıcıyı kilitlemesin
    return context


async def run_session(browser: Any, settings: Settings) -> Outcome:
    """Giriş + dört takvimin taranması."""
    context = await new_context(browser, settings)
    page = await context.new_page()
    try:
        logged_in = await ensure_login(context, page, settings)
    except Exception as exc:
        log.error("Siteye ulaşılamadı (%s).", type(exc).__name__)
        return Outcome("unreachable", message="Quietvox sitesine ulaşılamadı; eski veriler gösteriliyor.")
    if not logged_in:
        await dump_debug(settings, page, "login_failed")
        return Outcome("login_failed")
    results, detail_errors = await scrape_all(page, settings)
    return Outcome("ok", results, detail_errors)


def handle_login_failure(settings: Settings, previous: dict[str, Any] | None, now: datetime) -> int:
    """Giriş başarısız: bir kez bildir, siparişleri koruyarak durumu yaz, hata koduyla çık."""
    log.error("Quietvox oturumu açılamadı.")
    already_reported = bool(previous) and previous.get("status") == STATUS_LOGIN_FAILED
    if not already_reported:
        send_ntfy(settings, LOGIN_FAILED_MESSAGE, priority="high")
    write_status_only(settings, previous, STATUS_LOGIN_FAILED,
                      "Quietvox oturumu açılamadı, şifreyi kontrol edin.", now)
    return EXIT_LOGIN


def finish(settings: Settings, previous: dict[str, Any] | None, outcome: Outcome) -> int:
    """Oturum sonucunu data.json'a ve bildirimlere dönüştürür; çıkış kodunu döner."""
    now = datetime.now(timezone.utc)
    if outcome.state == "login_failed":
        return handle_login_failure(settings, previous, now)
    if outcome.state == "unreachable":
        write_status_only(settings, previous, STATUS_PARTIAL, outcome.message, now)
        return EXIT_FAILURE
    document = assemble_document(previous, outcome.results, outcome.detail_errors, settings, now)
    write_if_changed(settings, previous, document, now)
    maybe_send_reminder(settings, document)
    all_failed = all(orders is None for orders in outcome.results.values())
    return EXIT_FAILURE if all_failed else EXIT_OK


async def run(settings: Settings) -> int:
    """Tarayıcıyı açar, oturumu yürütür, tarayıcıyı her durumda düzgün kapatır."""
    from playwright.async_api import async_playwright  # yalnızca gerçek çalıştırmada gerekir

    previous = load_previous(settings.output)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=settings.headless)
        try:
            outcome = await run_session(browser, settings)
        finally:
            await browser.close()
    return finish(settings, previous, outcome)


def main() -> int:
    settings = load_settings()
    configure_logging(settings)
    if not settings.user or not settings.password:
        log.error("QV_USER ve QV_PASS ortam değişkenleri zorunludur; çıkılıyor.")
        return EXIT_CONFIG
    start, end = window_bounds(settings.today, settings.window_days)
    log.info("Pencere: %s → %s (%s).", start.isoformat(), end.isoformat(), TIMEZONE_NAME)
    try:
        return asyncio.run(run(settings))
    except Exception as exc:
        log.error("Beklenmeyen hata (%s): %s", type(exc).__name__, exc)
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
