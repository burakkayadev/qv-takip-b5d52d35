#!/usr/bin/env python3
"""Quietvox'ta tek bir siparişin durumunu günceller (uygulamadaki butonların arka ucu).

İki işlem vardır:
  delivered -> Durum "Delivered" yapılır, "Self Delivery" ve "Self Return" işaretlenir.
  returned  -> Durum "Returned" yapılır, "Self Return" işaretlenir, iade adetleri doldurulur:
               sarf kalemleri (Battery AA, Earphone 3.5mm New Style) teslim adedinin en fazla
               %3'ü (yukarı yuvarlanır), diğer tüm kalemler teslim adediyle aynı.

Form, sitenin kendi "Edit Order" penceresi üzerinden doldurulur; böylece sitenin tüm
doğrulamaları aynen çalışır. Beklenmeyen bir onay penceresi çıkarsa işlem KAYDEDİLMEZ.

BAŞARI ÖLÇÜTÜ (3 aşamalı doğrulama): tıklama, pencerenin kapanması ya da HTTP 200 asla başarı
sayılmaz. Kayıttan sonra tarayıcı önbelleği kapalıyken takvim sayfası yeniden yüklenir ve durum
portaldan üç bağımsız yoldan yeniden okunur: (1) yeniden açılan düzenleme penceresindeki seçili
durum (DOM), (2) takvim feed'indeki ham durum kodu, (3) düzenleme formunun GET ile okunan seçili
durumu. Üçü de hedef durumu göstermiyorsa işlem BAŞARISIZ sayılır, ekran görüntüsü + DOM özeti
debug/ altına yazılır ve çıkış kodu 1 olur.

Sipariş portalda zaten hedef durumdaysa hiçbir alan değiştirilmez, KAYDEDİLMEZ (iade adetleri
ezilmez); yalnızca doğrulama yapılır.

Ortam değişkenleri: QV_ACTION, QV_ORDER_ID, QV_ORDER_DATE (YYYY-AA-GG, siparişin o takvimdeki
günü), QV_ORDER_IDS (toplu), DRY_RUN=1 (formu doldurur ama kaydetmez) ve scraper.py ile aynı
giriş değişkenleri.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import portal_audit
import scraper

log = logging.getLogger("qv_action")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2
EXIT_LOGIN = 3

CONSUMABLE_PERCENT = 3  # sarf kalemlerinde iade adedi: teslim adedinin en fazla yüzde kaçı
# Sarf kalemleri: model kodu ya da adıyla tanınır (QV1.38 = Battery AA, QV1.2S = Earphone New Style)
CONSUMABLE_PATTERNS = (r"\bQV1\.38\b", r"battery\s+aa", r"\bQV1\.2S\b", r"earphone\s+3[.,]5\s*mm\s+new\s+style")
SAVE_TIMEOUT_MS = 45000
EVENT_WAIT_STEPS = 40  # x 500 ms
VERIFY_ATTEMPTS = 3  # kayıttan sonra portaldan yeniden okuma denemesi
VERIFY_PAUSE_MS = 2500
DEBUG_DIR = Path("debug")


@dataclass(frozen=True)
class ActionSpec:
    """Bir işlemin Quietvox karşılığı."""

    status_value: str  # Order[status] seçeneğinin değeri (feed'deki ham kodla aynı)
    status_name: str  # feed'deki durum adı
    kind: str  # hangi takvimde aranır: "delivery" | "return"
    calendar_path: str
    label: str  # formdaki seçeneğin görünen adı ve bildirim metni


ACTIONS = {
    # Form değerleri canlı sitede doğrulandı: 2 = Delivered, 6 = Returned (5 = Confirmed'dır!)
    "delivered": ActionSpec("2", "delivered", "delivery", "/calendar/deliveryCalendar.html", "Delivered"),
    "returned": ActionSpec("6", "returned", "return", "/calendar/returnCalendar.html", "Returned"),
}


@dataclass(frozen=True)
class Verification:
    """Kayıttan SONRA, önbelleksiz yeniden yüklemeyle portaldan yapılan üç bağımsız okuma."""

    dom_value: str  # yeniden açılan düzenleme penceresinde seçili durum kodu
    dom_label: str  # ... ve görünen adı
    feed_codes: tuple[str, ...]  # takvim feed'indeki ham durum kod(lar)ı (o gün, o sipariş)
    form_value: str  # düzenleme formunun GET ile okunan seçili durum kodu
    error: str = ""

    def ok(self, spec: ActionSpec) -> bool:
        """Üç okuma da hedef durumu gösteriyorsa True; biri bile eksik/farklıysa False."""
        return (not self.error
                and self.dom_value == spec.status_value and self.dom_label == spec.label
                and bool(self.feed_codes) and all(code == spec.status_value for code in self.feed_codes)
                and self.form_value == spec.status_value)

    def describe(self) -> str:
        text = (f"pencere={self.dom_value or '-'}/{self.dom_label or '-'}, "
                f"feed={','.join(self.feed_codes) or '-'}, form={self.form_value or '-'}")
        return f"{text}, hata={self.error}" if self.error else text


# Takvimde yalnızca hedef siparişi bırakır; böylece tıklanacak tek bir etkinlik kalır.
JS_ISOLATE_EVENT = r"""
async ({id, year, month, day, steps}) => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const calendar = window.jQuery && window.jQuery('#extended_calendar');
  if (!calendar || !calendar.length || !calendar.fullCalendar) return {error: 'takvim bulunamadı'};
  calendar.fullCalendar('gotoDate', year, month, day);
  let found = false;
  for (let i = 0; i < steps && !found; i++) {
    found = calendar.fullCalendar('clientEvents').some((event) => String(event.id) === id);
    if (!found) await sleep(500);
  }
  if (!found) return {error: 'sipariş takvimde bulunamadı'};
  calendar.fullCalendar('removeEvents', (event) => String(event.id) !== id);
  await sleep(600);
  return {count: document.querySelectorAll('#extended_calendar .fc-event').length};
}
"""

JS_READ_ITEMS = r"""
() => {
  const rows = [];
  for (let i = 0; i < 200; i++) {
    const delivered = document.querySelector(`#order-form [name="OrderDeviceItem[${i}][delivery_count]"]`);
    if (!delivered) break;
    const model = document.querySelector(`#order-form [name="OrderDeviceItem[${i}][device_item_id]"]`);
    const option = model && model.selectedOptions && model.selectedOptions[0];
    rows.push({index: i, delivered: delivered.value.trim(), model: option ? option.textContent.trim() : ''});
  }
  return rows;
}
"""

JS_FORM_PROBLEMS = r"""
() => {
  const visible = (node) => !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
  const texts = [];
  let clickedConfirm = '';
  const errors = '#order-form .errorSummary, #order-form .help-inline.error, #order-form .alert-error, .alert-error';
  for (const node of document.querySelectorAll(errors)) {
    if (visible(node) && node.textContent.trim()) texts.push(node.textContent.trim().replace(/\s+/g, ' '));
  }
  const extra = [...document.querySelectorAll('.modal.in')].filter((modal) => modal.id !== 'dialog_large');
  for (const modal of extra) {
    // "Send sms to the Tour Guide after saving the Order?" penceresinde SMS göndermeden kaydet ("No, just save changes")
    const noSmsBtn = [...modal.querySelectorAll('button, a, input[type="button"], input[type="submit"]')].find(
      (btn) => /no,\s*just\s*save/i.test((btn.textContent || btn.value || '').trim())
    );
    const saveChangedBtn = modal.querySelector('#save-if-changed-dialog-no-discard-save-btn');
    const autoBtn = noSmsBtn || saveChangedBtn;
    if (autoBtn) {
      if (!modal.dataset.qvConfirmed) {
        modal.dataset.qvConfirmed = String(Date.now());
        clickedConfirm = (autoBtn.textContent || autoBtn.value || 'Onay').trim();
        autoBtn.click();
      }
      if (Date.now() - Number(modal.dataset.qvConfirmed) < 15000) {
        continue;
      }
    }
    texts.push('Onay penceresi: ' + modal.textContent.trim().replace(/\s+/g, ' '));
  }
  const open = !!document.querySelector('#dialog_large.in');
  return {open, clickedConfirm, texts: texts.slice(0, 5).map((text) => text.slice(0, 300))};
}
"""

# Hata kanıtı: müşteri bilgisi yerine yalnızca durum alanı, pencere başlığı ve hata metinleri
JS_DOM_SUMMARY = r"""
() => {
  const status = document.querySelector('#Order_status');
  const header = document.querySelector('#dialog_large .modal-header');
  const errors = [...document.querySelectorAll('.errorSummary, .help-inline.error, .alert-error, .alert-danger')]
    .map((node) => node.textContent.trim().replace(/\s+/g, ' ')).filter(Boolean).slice(0, 5);
  return {
    url: location.origin + location.pathname,
    dialog_open: !!document.querySelector('#dialog_large.in'),
    heading: header ? header.textContent.trim().replace(/\s+/g, ' ').slice(0, 120) : '',
    status_value: status ? status.value : null,
    status_options: status ? [...status.options].map((o) => o.value + '=' + o.textContent.trim()) : [],
    open_modals: [...document.querySelectorAll('.modal.in')].map((m) => m.id || '(adsız)'),
    errors,
  };
}
"""


def is_consumable(model: str) -> bool:
    """Kalem sarf malzemesi mi? (pil ve tek kullanımlık kulaklık)"""
    return any(re.search(pattern, model, re.I) for pattern in CONSUMABLE_PATTERNS)


def planned_return_count(model: str, delivered: str) -> str | None:
    """İade adedini hesaplar; teslim adedi sayı değilse None (alan olduğu gibi bırakılır)."""
    if not delivered.isdigit():
        return None
    count = int(delivered)
    if is_consumable(model):
        return str(-(-count * CONSUMABLE_PERCENT // 100))  # yukarı yuvarlama
    return str(count)


def read_request(env: dict[str, str]) -> tuple[ActionSpec, str, date] | str:
    """Ortam değişkenlerinden isteği okur ve doğrular; hata varsa mesaj döner."""
    spec = ACTIONS.get((env.get("QV_ACTION") or "").strip().lower())
    order_id = (env.get("QV_ORDER_ID") or "").strip()
    day_text = (env.get("QV_ORDER_DATE") or "").strip()
    if spec is None:
        return "QV_ACTION 'delivered' ya da 'returned' olmalı."
    if not re.fullmatch(r"\d{4,15}", order_id):
        return "QV_ORDER_ID yalnızca rakamlardan oluşmalı."
    if not scraper.valid_iso(day_text):
        return "QV_ORDER_DATE YYYY-AA-GG biçiminde olmalı."
    return spec, order_id, date.fromisoformat(day_text)


def read_requests(env: dict[str, str]) -> tuple[ActionSpec, list[tuple[str, date]]] | str:
    """Tekli (QV_ORDER_ID + QV_ORDER_DATE) veya toplu (QV_ORDER_IDS: 'id:YYYY-AA-GG,...') istekleri okur."""
    spec = ACTIONS.get((env.get("QV_ACTION") or "").strip().lower())
    if spec is None:
        return "QV_ACTION 'delivered' ya da 'returned' olmalı."
    default_date = (env.get("QV_ORDER_DATE") or "").strip()
    raw_batch = (env.get("QV_ORDER_IDS") or "").strip()
    if not raw_batch:
        raw_id = (env.get("QV_ORDER_ID") or "").strip()
        if "," in raw_id or ":" in raw_id:
            raw_batch = raw_id
    if raw_batch:
        items: list[tuple[str, date]] = []
        seen: set[str] = set()
        for part in raw_batch.split(","):
            token = part.strip()
            if not token:
                continue
            if ":" in token:
                oid, dtext = token.split(":", 1)
                oid, dtext = oid.strip(), dtext.strip()
            else:
                oid, dtext = token, default_date
            if not re.fullmatch(r"\d{4,15}", oid):
                return f"QV_ORDER_IDS içinde geçersiz sipariş kimliği: {oid}"
            if not scraper.valid_iso(dtext):
                return f"QV_ORDER_IDS içinde geçersiz tarih ({oid}): YYYY-AA-GG olmalı."
            key = f"{oid}:{dtext}"
            if key not in seen:
                seen.add(key)
                items.append((oid, date.fromisoformat(dtext)))
        if not items:
            return "QV_ORDER_IDS boş olamaz."
        return spec, items
    single = read_request(env)
    if isinstance(single, str):
        return single
    s_spec, s_id, s_day = single
    return s_spec, [(s_id, s_day)]


def feed_codes(payload: Any, order_id: str, day: date) -> list[str]:
    """Feed'de siparişin o günkü HAM durum kod(lar)ı; hiçbir sezgisel dönüşüm uygulanmaz."""
    if not isinstance(payload, list):
        raise ValueError("feed biçimi tanınmadı")
    codes: list[str] = []
    for entry in payload:
        if not isinstance(entry, dict) or str(entry.get("id")) != order_id:
            continue
        if scraper.valid_iso(scraper._feed_start(entry.get("start"))[:10]) == day.isoformat():
            codes.append(str(entry.get("status")))
    return codes


async def disable_http_cache(page: Any) -> None:
    """Tarayıcı önbelleğini kapatır: her yeniden yükleme sunucudan taze okunur (Chromium CDP)."""
    try:
        session = await page.context.new_cdp_session(page)
        await session.send("Network.enable")
        await session.send("Network.setCacheDisabled", {"cacheDisabled": True})
    except Exception as exc:
        log.warning("Tarayıcı önbelleği kapatılamadı (%s); adreslere önbellek kırıcı eklenmeye devam edilir.",
                    type(exc).__name__)


async def open_edit_dialog(page: Any, settings: scraper.Settings, spec: ActionSpec,
                           order_id: str, day: date) -> None:
    """Takvimi önbelleksiz açar, siparişi bulur ve "Edit Order" penceresini açar; doğru sipariş mi doğrular."""
    await scraper.goto(page, portal_audit.cache_busted(settings.base_url + spec.calendar_path), settings)
    await page.wait_for_selector("#extended_calendar .fc-header", timeout=settings.nav_timeout)
    result = await page.evaluate(JS_ISOLATE_EVENT, {
        "id": order_id, "year": day.year, "month": day.month - 1, "day": day.day,
        "steps": EVENT_WAIT_STEPS})
    if result.get("error"):
        raise RuntimeError(result["error"])
    if result.get("count") != 1:
        raise RuntimeError(f"takvimde tek etkinlik bekleniyordu, {result.get('count')} bulundu")
    # Düzenle simgesi yalnızca fare etkinliğin üzerindeyken görünür; bu yüzden görünürlük
    # beklenmez, sitenin kendi tıklama işleyicisi doğrudan tetiklenir.
    await page.hover("#extended_calendar .fc-event")
    await page.evaluate("document.querySelector('#extended_calendar .fc-event a.edit-order').click()")
    await page.wait_for_selector("#dialog_large.in #order-form #Order_status", timeout=settings.nav_timeout)
    heading = await page.inner_text("#dialog_large")
    if not re.search(rf"Update Order\s+{order_id}\b", heading):
        raise RuntimeError("açılan pencere beklenen siparişe ait değil")


async def close_dialog(page: Any) -> None:
    """Düzenleme penceresini kaydetmeden kapatır (hiçbir alan değiştirilmemişken kullanılır)."""
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(400)


async def set_status(page: Any, spec: ActionSpec) -> bool:
    """Durumu seçer; zaten o durumdaysa False döner. Seçenek yoksa hata verir."""
    current = await page.eval_on_selector("#Order_status", "el => el.value")
    options = await page.eval_on_selector_all("#Order_status option", "els => els.map(el => el.value)")
    log.info("Mevcut durum kodu: %s; seçilebilir kodlar: %s", current, ", ".join(options))
    if current == spec.status_value:
        return False
    if spec.status_value not in options:
        names = await page.eval_on_selector_all(
            "#Order_status option", "els => els.map(el => el.textContent.trim())")
        raise RuntimeError(f"bu sipariş şu an '{spec.label}' yapılamıyor (seçenekler: {', '.join(names)})")
    await page.select_option("#Order_status", spec.status_value)
    # Güvenlik: seçilen seçeneğin görünen adı beklenen durumla aynı değilse hiçbir şey kaydetme
    chosen = await page.eval_on_selector("#Order_status", "el => el.selectedOptions[0].textContent.trim()")
    if chosen != spec.label:
        raise RuntimeError(f"durum seçimi doğrulanamadı (seçilen: {chosen})")
    return True


async def fill_return_counts(page: Any) -> list[str]:
    """İade adetlerini kurala göre doldurur; yapılanların özetini döner."""
    summary = []
    for row in await page.evaluate(JS_READ_ITEMS):
        planned = planned_return_count(row["model"], row["delivered"])
        if planned is None:
            continue
        await page.fill(f'#order-form [name="OrderDeviceItem[{row["index"]}][return_count]"]', planned)
        summary.append(f"{row['model'][:28]}: {row['delivered']} -> {planned}")
    return summary


async def save_and_check(page: Any, dialogs: list[str]) -> str:
    """Kaydeder; pencere kapanırsa boş string, sorun varsa açıklamasını döner.

    Pencerenin kapanması BAŞARI DEĞİLDİR; yalnızca "kaydetme denendi" demektir. Başarı
    verify_on_portal ile kanıtlanır.
    """
    # Pencerenin kendi alt çubuğundaki Save; içerideki "save-if-changed" onay düğmeleri hariç tutulur
    save = page.locator('#dialog_large > .modal-footer .btn-primary:not([id^="save-if-changed"])')
    if await save.count() != 1:
        return f"Save düğmesi tek değil ({await save.count()} adet); kaydedilmedi"
    await save.click()
    waited = 0
    while waited < SAVE_TIMEOUT_MS:
        await page.wait_for_timeout(500)
        waited += 500
        if dialogs:
            return "Beklenmeyen onay sorusu (reddedildi): " + dialogs[0][:200]
        state = await page.evaluate(JS_FORM_PROBLEMS)
        if state.get("clickedConfirm"):
            log.info("Onay penceresi otomatik onaylandı: %s", state["clickedConfirm"])
        if state["texts"]:
            return "; ".join(state["texts"])
        if not state["open"]:
            return ""
    return "kaydetme zaman aşımına uğradı"


async def read_dialog_status(page: Any, settings: scraper.Settings, spec: ActionSpec,
                             order_id: str, day: date) -> tuple[str, str]:
    """Okuma 1 (DOM): takvim önbelleksiz yeniden yüklenir, pencere yeniden açılır, seçili durum okunur."""
    await open_edit_dialog(page, settings, spec, order_id, day)
    value = await page.eval_on_selector("#Order_status", "el => el.value")
    label = await page.eval_on_selector(
        "#Order_status", "el => el.selectedOptions[0] ? el.selectedOptions[0].textContent.trim() : ''")
    await close_dialog(page)
    return str(value), str(label)


async def read_feed_codes(page: Any, settings: scraper.Settings, spec: ActionSpec,
                          order_id: str, day: date) -> list[str]:
    """Okuma 2 (API): takvim feed'indeki ham durum kodu (önbellek kırıcı ile)."""
    url = scraper.feed_url_for(settings.base_url + spec.calendar_path, spec.kind, day, 1)
    payload = json.loads(await scraper._get_text(page.context, portal_audit.cache_busted(url), settings))
    return feed_codes(payload, order_id, day)


async def read_form_value(page: Any, settings: scraper.Settings, order_id: str) -> str:
    """Okuma 3 (API): düzenleme formunun GET ile okunan seçili durum kodu (kaydetmez)."""
    query = urllib.parse.urlencode({"id": order_id})
    url = portal_audit.cache_busted(f"{settings.base_url}{portal_audit.FORM_PATH}?{query}")
    form = portal_audit.parse_form_status(await scraper._get_text(page.context, url, settings), order_id)
    if form.get("error"):
        raise RuntimeError(form["error"])
    return str(form["value"])


async def verify_on_portal(page: Any, settings: scraper.Settings, spec: ActionSpec,
                           order_id: str, day: date) -> Verification:
    """Kaydın portalda kalıcı olduğunu kanıtlar; en fazla VERIFY_ATTEMPTS kez yeniden okur."""
    result = Verification("", "", (), "", "doğrulama yapılmadı")
    for attempt in range(1, VERIFY_ATTEMPTS + 1):
        try:
            dom_value, dom_label = await read_dialog_status(page, settings, spec, order_id, day)
            codes = tuple(await read_feed_codes(page, settings, spec, order_id, day))
            result = Verification(dom_value, dom_label, codes, await read_form_value(page, settings, order_id))
        except Exception as exc:
            result = Verification("", "", (), "", f"{type(exc).__name__}: {exc}"[:200])
        log.info("Doğrulama %d/%d — sipariş %s hedef %s: %s", attempt, VERIFY_ATTEMPTS, order_id,
                 spec.label, result.describe())
        if result.ok(spec):
            return result
        if attempt < VERIFY_ATTEMPTS:
            await page.wait_for_timeout(VERIFY_PAUSE_MS)
    return result


async def verified_problem(page: Any, settings: scraper.Settings, spec: ActionSpec,
                           order_id: str, day: date) -> str:
    """Portal doğrulaması geçerse boş string, geçmezse ayrıntılı hata metni döner."""
    result = await verify_on_portal(page, settings, spec, order_id, day)
    if result.ok(spec):
        log.info("Sipariş %s '%s' portalda DOĞRULANDI (%s).", order_id, spec.label, result.describe())
        return ""
    return f"portalda doğrulanamadı: '{spec.label}' görünmüyor ({result.describe()})"


async def apply_action(page: Any, settings: scraper.Settings, spec: ActionSpec,
                       order_id: str, day: date, dry_run: bool) -> str:
    """İşlemi uygular ve portalda doğrular; başarılıysa boş string, değilse hata açıklaması döner."""
    dialogs: list[str] = []

    async def on_dialog(dialog: Any) -> None:
        if dialog.type == "beforeunload":
            await dialog.accept()  # doğrulama için sayfa yeniden yüklenirken (kayıt sonrası) güvenli
            return
        dialogs.append(dialog.message)
        await dialog.dismiss()  # tanımadığımız hiçbir soruya "evet" denmez

    page.on("dialog", on_dialog)
    try:
        await open_edit_dialog(page, settings, spec, order_id, day)
        current = await page.eval_on_selector("#Order_status", "el => el.value")
        if current == spec.status_value:
            log.info("Sipariş %s portalda zaten '%s'; hiçbir alan değiştirilmedi, KAYDEDİLMEDİ.",
                     order_id, spec.label)
            await close_dialog(page)
            return await verified_problem(page, settings, spec, order_id, day)
        await set_status(page, spec)
        if spec.status_name == "delivered":
            await page.check("#Order_is_self_delivery_1")
        await page.check("#Order_is_self_return_1")
        if spec.status_name == "returned":
            for line in await fill_return_counts(page):
                log.info("İade adedi: %s", line)
        if dry_run:
            log.info("DRY_RUN: form dolduruldu, KAYDEDİLMEDİ (doğrulama yapılmadı).")
            return ""
        problem = await save_and_check(page, dialogs)
        if problem:
            return problem
        return await verified_problem(page, settings, spec, order_id, day)
    finally:
        page.remove_listener("dialog", on_dialog)


async def save_failure_evidence(page: Any, order_id: str, spec: ActionSpec, problem: str) -> list[str]:
    """Başarısız işlemin ekran görüntüsünü ve DOM özetini debug/ altına yazar (artifact olarak yüklenir)."""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = DEBUG_DIR / f"action_{order_id}_{spec.status_name}_{stamp}"
    saved: list[str] = []
    try:
        await page.screenshot(path=f"{base}.png")
        saved.append(f"{base}.png")
    except Exception as exc:
        log.warning("Ekran görüntüsü alınamadı (%s).", type(exc).__name__)
    try:
        summary = await page.evaluate(JS_DOM_SUMMARY)
    except Exception as exc:
        summary = {"error": f"DOM okunamadı ({type(exc).__name__})"}
    evidence = {"order_id": order_id, "action": spec.status_name, "problem": problem, "at": stamp, "dom": summary}
    Path(f"{base}.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    saved.append(f"{base}.json")
    return saved


async def process_order(page: Any, settings: scraper.Settings, spec: ActionSpec,
                        order_id: str, day: date, dry_run: bool) -> str:
    """Tek siparişi en fazla 3 kez dener; son hata metnini (başarıda boş string) döner."""
    problem = ""
    for attempt in range(1, 4):
        try:
            problem = await apply_action(page, settings, spec, order_id, day, dry_run)
        except Exception as exc:
            problem = str(exc) or type(exc).__name__
        if not problem:
            return ""
        if attempt < 3:
            log.warning("Sipariş %s deneme %d başarısız (%s); yeniden deneniyor...", order_id, attempt, problem)
            await page.wait_for_timeout(1500)
    files = await save_failure_evidence(page, order_id, spec, problem)
    log.error("Sipariş %s '%s' YAPILAMADI: %s | kanıt: %s", order_id, spec.label, problem, ", ".join(files))
    return problem


def notify_results(settings: scraper.Settings, spec: ActionSpec, succeeded: list[str],
                   failures: list[tuple[str, str]], dry_run: bool) -> None:
    """Yalnızca portalda doğrulanan siparişler için başarı, diğerleri için hata bildirimi gönderir."""
    if not dry_run and succeeded:
        if len(succeeded) == 1:
            scraper.send_ntfy(settings, f"✅ Sipariş {succeeded[0]} Quietvox'ta {spec.label} — portalda doğrulandı.")
        else:
            scraper.send_ntfy(settings, f"✅ {len(succeeded)} sipariş Quietvox'ta {spec.label} — portalda "
                                        f"doğrulandı: {', '.join(succeeded[:8])}")
    if not failures or (os.environ.get("QV_NOTIFY_FAILURE") or "1").strip() == "0":
        return
    for oid, prob in failures[:5]:
        scraper.send_ntfy(settings, f"❌ Sipariş {oid} Quietvox'a {spec.label} olarak İŞLENEMEDİ: {prob[:200]}",
                          priority="high")
    try:
        import notify_mail
        notify_mail.send_action_failure_alert(failures, spec.label, os.environ)
    except Exception as exc:
        log.warning("Hata uyarı e-postası gönderilemedi: %s", exc)


async def run_many(settings: scraper.Settings, spec: ActionSpec,
                   orders: list[tuple[str, date]], dry_run: bool) -> int:
    from playwright.async_api import async_playwright

    succeeded: list[str] = []
    failures: list[tuple[str, str]] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=settings.headless)
        try:
            # Ana şifre reddedilirse yedek şifre (QV_PASS_BACKUP) denenir. Alarm e-postasını
            # iş sonunda tetiklenen senkron (sync.yml) gönderir.
            session = await scraper.login_with_fallback(browser, settings)
            if session is None:
                scraper.send_ntfy(settings, scraper.LOGIN_FAILED_MESSAGE, priority="high")
                return EXIT_LOGIN
            page, active, _ = session
            await disable_http_cache(page)
            for order_id, day in orders:
                problem = await process_order(page, active, spec, order_id, day, dry_run)
                if problem:
                    failures.append((order_id, problem))
                else:
                    succeeded.append(order_id)
        finally:
            await browser.close()
    notify_results(settings, spec, succeeded, failures, dry_run)
    log.info("SONUÇ: %d doğrulandı, %d başarısız.", len(succeeded), len(failures))
    return EXIT_FAILED if failures else EXIT_OK


async def run(settings: scraper.Settings, spec: ActionSpec, order_id: str, day: date, dry_run: bool) -> int:
    return await run_many(settings, spec, [(order_id, day)], dry_run)


def main() -> int:
    settings = scraper.load_settings()
    scraper.configure_logging(settings)
    if not (settings.user and settings.password):
        log.error("QV_USER ve QV_PASS tanımlı olmalı.")
        return EXIT_CONFIG
    request = read_requests(dict(os.environ))
    if isinstance(request, str):
        log.error(request)
        return EXIT_CONFIG
    spec, orders = request
    dry_run = (os.environ.get("DRY_RUN") or "").strip().lower() in ("1", "true", "yes")
    return asyncio.run(run_many(settings, spec, orders, dry_run))


if __name__ == "__main__":
    sys.exit(main())
