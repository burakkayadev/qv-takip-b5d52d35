#!/usr/bin/env python3
"""Quietvox portalını SALT OKUNUR denetler ve data.json ile karşılaştırır.

Portalda hiçbir şey değiştirilmez: yalnızca GET istekleri atılır (takvim feed'i ve
siparişin düzenleme formu). Amaç, uygulamanın gösterdiği durumun portalın gerçek
durumu olup olmadığını kanıtlamaktır.

  * Dört takvimin ham feed'i önbelleksiz okunur; durum kodu hiçbir tahmin/sezgisel
    dönüşüm uygulanmadan alınır (scraper.resolve_order_status KULLANILMAZ).
  * Hedef siparişlerin ve uyuşmazlıkların düzenleme formundaki seçili durum okunur
    (portal ekranında görünen durum).
  * Durum kodu -> ad eşlemesi canlı formla doğrulanır.
  * data.json ile karşılaştırılır; her uyuşmazlık tek tek raporlanır, HİÇBİRİ düzeltilmez.

Kullanım: python portal_audit.py --data data.json --ids 2000221582,2000221583 --out debug/portal_audit.json
Çıkış kodu: 0 = tam uyum, 1 = uyuşmazlık/okuma hatası, 2 = yapılandırma hatası, 3 = giriş başarısız.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
import urllib.parse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from bs4 import BeautifulSoup

import scraper

log = logging.getLogger("portal_audit")

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_CONFIG = 2
EXIT_LOGIN = 3

FORM_PATH = "/order/segmentUpdate"
FORM_CONCURRENCY = 4  # siteyi yormamak için aynı anda en fazla form isteği
MAX_FORM_READS = 80  # tek çalıştırmada okunacak en fazla düzenleme formu
SAMPLES_PER_CODE = 2  # durum eşlemesi doğrulamasında her kod için örnek sipariş
RECENT_DAYS = 14  # raporda "son 14 gün" ayrımı
DEFAULT_TARGETS = ("2000221582", "2000221583", "2000221686")
LIST_KEYS = ("deliveries", "returns")
_REPEAT_SUFFIX = re.compile(r"_\d{4}-\d{2}-\d{2}$")  # scraper.separate_repeated_ids eki


@dataclass(frozen=True)
class PortalEvent:
    """Takvim feed'indeki tek bir etkinlik (ham durum kodu ile)."""

    calendar: str  # data.json'daki source_calendar ile aynı ad
    order_id: str
    day: str  # YYYY-AA-GG
    code: str  # feed'deki ham "status" değeri
    name: str  # kodun adı (ORDER_STATUS_NAMES); bilinmiyorsa ham değer


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def cache_busted(url: str) -> str:
    """Ara katman önbelleklerini atlatmak için adrese benzersiz parametre ekler."""
    joiner = "&" if "?" in url else "?"
    return f"{url}{joiner}_={int(time.time() * 1000)}"


def raw_events(payload: Any, calendar: str, low: date, high: date) -> list[PortalEvent]:
    """Feed JSON'unu ham etkinliklere çevirir; durum koduna DOKUNMAZ."""
    if not isinstance(payload, list):
        raise ValueError("feed biçimi tanınmadı")
    events: list[PortalEvent] = []
    for entry in payload:
        if not isinstance(entry, dict) or entry.get("id") in (None, ""):
            continue
        day = scraper.valid_iso(scraper._feed_start(entry.get("start"))[:10])
        if not day or not (low.isoformat() <= day <= high.isoformat()):
            continue
        raw = entry.get("status")
        code = "" if raw is None else str(raw).strip()
        events.append(PortalEvent(calendar, str(entry["id"]).strip(), day, code,
                                  scraper.order_status_name(raw) or code))
    return events


def parse_form_status(html: str, order_id: str) -> dict[str, Any]:
    """Düzenleme formundaki seçili durumu okur (portal ekranında görünen durum)."""
    select = BeautifulSoup(html, "html.parser").select_one("#Order_status")
    if select is None:
        return {"error": "formda durum alanı (#Order_status) yok"}
    options = select.find_all("option")
    chosen = next((opt for opt in options if opt.has_attr("selected")), options[0] if options else None)
    return {
        "value": (chosen.get("value") or "").strip() if chosen else "",
        "label": chosen.get_text(strip=True) if chosen else "",
        "options": [(opt.get("value") or "").strip() for opt in options],
        "mentions_id": order_id in html,
    }


async def read_form(context: Any, settings: scraper.Settings, order_id: str,
                    gate: asyncio.Semaphore) -> dict[str, Any]:
    """Siparişin düzenleme formunu GET ile okur (kaydetmez)."""
    query = urllib.parse.urlencode({"id": order_id})
    url = cache_busted(f"{settings.base_url}{FORM_PATH}?{query}")
    async with gate:
        try:
            html = await scraper._get_text(context, url, settings)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"[:200]}
    return parse_form_status(html, order_id)


async def fetch_calendar(context: Any, settings: scraper.Settings, spec: scraper.CalendarSpec,
                         urls: list[str], past_days: int, future_days: int) -> tuple[str, list[PortalEvent]]:
    """Takvimin ham feed'ini önbelleksiz okur; okunamazsa RuntimeError."""
    low, high = scraper.window_bounds(settings.today, future_days, past_days)
    problems_seen: list[str] = []
    for url in urls:
        feed = cache_busted(scraper.feed_url_for(url, spec.kind, settings.today, future_days, past_days))
        try:
            payload = json.loads(await scraper._get_text(context, feed, settings))
            return url, raw_events(payload, spec.name, low, high)
        except Exception as exc:
            problems_seen.append(f"{urllib.parse.urlsplit(url).path}: {type(exc).__name__}")
    raise RuntimeError(f"{spec.name} feed'i okunamadı ({'; '.join(problems_seen) or 'aday adres yok'})")


async def collect_events(page: Any, settings: scraper.Settings, past_days: int, future_days: int,
                         report: dict[str, Any]) -> list[PortalEvent]:
    """Dört takvimin ham etkinliklerini toplar; okunamayan takvim rapora hata olarak yazılır."""
    discovered = await scraper.discover_calendar_urls(page, settings)
    events: list[PortalEvent] = []
    for spec in scraper.CALENDARS:
        urls = scraper.candidate_urls(spec, settings, discovered)
        try:
            url, found = await fetch_calendar(page.context, settings, spec, urls, past_days, future_days)
        except RuntimeError as exc:
            report["errors"].append(str(exc))
            continue
        events.extend(found)
        report["calendars"][spec.name] = {"path": urllib.parse.urlsplit(url).path, "events": len(found),
                                          "codes": dict(Counter(event.code for event in found))}
    return events


def load_document(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ValueError("data.json beklenen biçimde değil")
    return document


def document_statuses(document: dict[str, Any], low: date, high: date) -> dict[tuple[str, str, str], str]:
    """data.json'daki (takvim, QV ID, gün) -> durum eşlemesi (pencere içi)."""
    result: dict[tuple[str, str, str], str] = {}
    for key in LIST_KEYS:
        for order in document.get(key) or []:
            if not isinstance(order, dict):
                continue
            day = str(order.get("date") or "")
            if not (low.isoformat() <= day <= high.isoformat()):
                continue
            order_id = _REPEAT_SUFFIX.sub("", str(order.get("id") or ""))
            result[(str(order.get("source_calendar") or ""), order_id, day)] = str(order.get("status") or "")
    return result


def compare(events: Iterable[PortalEvent], statuses: dict[tuple[str, str, str], str],
            today: date) -> list[dict[str, Any]]:
    """Portal ham durumu ile data.json durumunu tek tek karşılaştırır; uyuşmazlıkları döner."""
    portal = {(event.calendar, event.order_id, event.day): event for event in events}
    recent_from = (today - timedelta(days=RECENT_DAYS)).isoformat()
    rows: list[dict[str, Any]] = []
    for key in sorted(set(portal) | set(statuses), key=lambda k: (k[2], k[0], k[1])):
        calendar, order_id, day = key
        event = portal.get(key)
        mine = statuses.get(key)
        if event is not None and mine == event.name:
            continue
        if event is None:
            reason = "data.json'da var, portal feed'inde yok"
        elif mine is None:
            reason = "portalda var, data.json'da yok"
        elif mine == "returned":
            reason = f"uygulama 'returned' gösteriyor ama portal '{event.name}'"
        else:
            reason = f"durum farklı (portal '{event.name}')"
        rows.append({"calendar": calendar, "id": order_id, "date": day, "recent": day >= recent_from,
                     "data_json": mine, "portal_code": event.code if event else None,
                     "portal": event.name if event else None, "reason": reason})
    return rows


def mapping_samples(events: Iterable[PortalEvent], per_code: int = SAMPLES_PER_CODE) -> dict[str, list[str]]:
    """Her ham durum kodu için birkaç örnek sipariş seçer (eşleme doğrulaması için)."""
    samples: dict[str, list[str]] = {}
    for event in events:
        ids = samples.setdefault(event.code, [])
        if event.order_id not in ids and len(ids) < per_code:
            ids.append(event.order_id)
    return samples


def verify_mapping(samples: dict[str, list[str]], forms: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Feed kodu, formdaki seçili kod ve adı aynı mı? (ORDER_STATUS_NAMES doğrulaması)"""
    rows = []
    for code, ids in sorted(samples.items()):
        expected = scraper.order_status_name(code) or code
        for order_id in ids:
            form = forms.get(order_id)
            if form is None:
                continue
            label = str(form.get("label") or "").casefold()
            rows.append({"code": code, "expected": expected, "id": order_id,
                         "form_value": form.get("value"), "form_label": form.get("label"),
                         "ok": not form.get("error") and form.get("value") == code and label == expected})
    return rows


def split_statuses(events: Iterable[PortalEvent]) -> dict[str, dict[str, str]]:
    """Aynı sipariş farklı takvimlerde farklı kodla görünüyorsa listeler."""
    by_id: dict[str, dict[str, str]] = {}
    for event in events:
        by_id.setdefault(event.order_id, {})[f"{event.calendar} {event.day}"] = event.code
    return {oid: codes for oid, codes in by_id.items() if len(set(codes.values())) > 1}


def forms_to_read(targets: list[str], mismatches: list[dict[str, Any]],
                  samples: dict[str, list[str]]) -> tuple[list[str], int]:
    """Okunacak formlar: hedefler, uyuşmazlıklar, eşleme örnekleri (sınırlı)."""
    wanted: list[str] = []
    for order_id in [*targets, *(row["id"] for row in mismatches),
                     *(oid for ids in samples.values() for oid in ids)]:
        if order_id and order_id not in wanted:
            wanted.append(order_id)
    return wanted[:MAX_FORM_READS], max(0, len(wanted) - MAX_FORM_READS)


def finalize_report(report: dict[str, Any], events: list[PortalEvent],
                    statuses: dict[tuple[str, str, str], str], mismatches: list[dict[str, Any]],
                    forms: dict[str, dict[str, Any]], targets: list[str]) -> None:
    """Form sonuçlarını uyuşmazlık ve hedef kayıtlarına ekler."""
    for row in mismatches:
        row["form"] = forms.get(row["id"])
    report["targets"] = {
        oid: {"feed": [asdict(event) for event in events if event.order_id == oid],
              "data_json": [{"calendar": cal, "date": day, "status": status}
                            for (cal, order_id, day), status in sorted(statuses.items()) if order_id == oid],
              "form": forms.get(oid)}
        for oid in targets}
    report["split_statuses"] = split_statuses(events)
    report["mismatches"] = mismatches


async def run_audit(settings: scraper.Settings, document: dict[str, Any], targets: list[str],
                    past_days: int, future_days: int) -> dict[str, Any]:
    """Salt okunur denetimi yürütür ve raporu döner (portalda hiçbir şey değiştirilmez)."""
    from playwright.async_api import async_playwright

    low, high = scraper.window_bounds(settings.today, future_days, past_days)
    report: dict[str, Any] = {
        "audited_at": utc_now(), "today": settings.today.isoformat(),
        "window": [low.isoformat(), high.isoformat()], "data_generated_at": document.get("generated_at"),
        "login": "failed", "calendars": {}, "errors": [], "targets": {}, "mapping": [],
        "split_statuses": {}, "mismatches": [], "form_reads_skipped": 0,
    }
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=settings.headless)
        try:
            session = await scraper.login_with_fallback(browser, settings)
            if session is None:
                return report
            page, active, _ = session
            report["login"] = "ok"
            events = await collect_events(page, active, past_days, future_days, report)
            statuses = document_statuses(document, low, high)
            mismatches = compare(events, statuses, active.today)
            samples = mapping_samples(events)
            wanted, report["form_reads_skipped"] = forms_to_read(targets, mismatches, samples)
            gate = asyncio.Semaphore(FORM_CONCURRENCY)
            results = await asyncio.gather(*(read_form(page.context, active, oid, gate) for oid in wanted))
            forms = dict(zip(wanted, results))
        finally:
            await browser.close()
    report["mapping"] = verify_mapping(samples, forms)
    finalize_report(report, events, statuses, mismatches, forms, targets)
    return report


def problems(report: dict[str, Any]) -> list[str]:
    """Raporu kırmızıya çeviren her sorunu tek satır olarak döner."""
    found: list[str] = []
    if report.get("login") != "ok":
        found.append("portal girişi başarısız")
    found.extend(f"okuma hatası: {err}" for err in report.get("errors", []))
    found.extend(f"uyuşmazlık: {row['calendar']} {row['date']} {row['id']} — {row['reason']}"
                 for row in report.get("mismatches", []))
    found.extend(f"durum eşlemesi tutmuyor: kod {row['code']} beklenen {row['expected']}, form "
                 f"{row['form_value']}/{row['form_label']} ({row['id']})"
                 for row in report.get("mapping", []) if not row["ok"])
    for oid, info in (report.get("targets") or {}).items():
        form = info.get("form") or {}
        if form.get("error"):
            found.append(f"hedef {oid} formu okunamadı: {form['error']}")
        if not info.get("feed"):
            found.append(f"hedef {oid} portal takvimlerinde bulunamadı")
    return found


def _form_text(form: dict[str, Any] | None) -> str:
    if not form:
        return "okunmadı"
    return form.get("error") or f"{form.get('value')}={form.get('label')}"


def log_report(report: dict[str, Any]) -> None:
    """Raporu okunabilir satırlar olarak günlüğe yazar (müşteri adı/telefon yazılmaz)."""
    log.info("Denetim: %s | bugün %s | pencere %s..%s | data.json generated_at %s",
             report["audited_at"], report["today"], *report["window"], report.get("data_generated_at"))
    for name, info in report.get("calendars", {}).items():
        log.info("Takvim %-26s %-38s etkinlik=%-4s kodlar=%s", name, info["path"], info["events"], info["codes"])
    log.info("== HEDEF SİPARİŞLER ==")
    for oid, info in report.get("targets", {}).items():
        feed = "; ".join(f"{e['calendar']} {e['day']} kod={e['code']}({e['name']})" for e in info["feed"])
        mine = "; ".join(f"{d['calendar']} {d['date']}={d['status']}" for d in info["data_json"])
        options = (info.get("form") or {}).get("options")
        log.info("%s | PORTAL FEED: %s | PORTAL FORM: %s seçenekler=%s | DATA.JSON: %s", oid,
                 feed or "feed'de yok", _form_text(info.get("form")), options, mine or "data.json'da yok")
    log.info("== DURUM KODU EŞLEMESİ (feed kodu = form seçimi?) ==")
    for row in report.get("mapping", []):
        log.info("kod %-3s beklenen %-10s %s form=%s/%s %s", row["code"], row["expected"], row["id"],
                 row["form_value"], row["form_label"], "OK" if row["ok"] else "HATA")
    for oid, codes in report.get("split_statuses", {}).items():
        log.info("Takvimler arası farklı kod: %s %s", oid, codes)
    mismatches = report.get("mismatches", [])
    log.info("== UYUŞMAZLIKLAR: %d (son %d gün: %d) ==", len(mismatches), RECENT_DAYS,
             sum(1 for row in mismatches if row["recent"]))
    for row in mismatches:
        log.info("%s %-26s %s %s data.json=%s portal=%s form=%s — %s", "[son14]" if row["recent"] else "[eski] ",
                 row["calendar"], row["date"], row["id"], row["data_json"], row["portal"],
                 _form_text(row.get("form")), row["reason"])
    if report.get("form_reads_skipped"):
        log.info("Sınır nedeniyle okunmayan form sayısı: %d", report["form_reads_skipped"])
    issues = problems(report)
    log.info("SONUÇ: %s", "TAM UYUM" if not issues else f"{len(issues)} SORUN")


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_ids(text: str) -> list[str] | str:
    ids: list[str] = []
    for token in (text or "").split(","):
        token = token.strip()
        if not token:
            continue
        if not re.fullmatch(r"\d{4,15}", token):
            return f"geçersiz QV ID: {token}"
        if token not in ids:
            ids.append(token)
    return ids


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Quietvox portalı salt okunur denetimi")
    parser.add_argument("--data", default="data.json", help="karşılaştırılacak data.json")
    parser.add_argument("--ids", default=",".join(DEFAULT_TARGETS), help="ayrıntılı incelenecek QV ID'ler")
    parser.add_argument("--past-days", type=int, default=62)
    parser.add_argument("--future-days", type=int, default=10)
    parser.add_argument("--out", default="debug/portal_audit.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = scraper.load_settings()
    scraper.configure_logging(settings)
    if not (settings.user and settings.password):
        log.error("QV_USER ve QV_PASS tanımlı olmalı.")
        return EXIT_CONFIG
    targets = parse_ids(args.ids)
    if isinstance(targets, str):
        log.error(targets)
        return EXIT_CONFIG
    try:
        document = load_document(Path(args.data))
    except (OSError, ValueError) as exc:
        log.error("data.json okunamadı: %s", exc)
        return EXIT_CONFIG
    report = asyncio.run(run_audit(settings, document, targets, args.past_days, args.future_days))
    write_report(report, Path(args.out))
    log_report(report)
    if report.get("login") != "ok":
        return EXIT_LOGIN
    return EXIT_MISMATCH if problems(report) else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
