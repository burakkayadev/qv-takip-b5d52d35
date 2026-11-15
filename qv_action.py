#!/usr/bin/env python3
"""Quietvox'ta tek bir siparişin durumunu günceller (uygulamadaki butonların arka ucu).

İki işlem vardır:
  delivered -> Durum "Delivered" yapılır, "Self Delivery" ve "Self Return" işaretlenir.
  returned  -> Durum "Returned" yapılır, "Self Return" işaretlenir, iade adetleri doldurulur:
               sarf kalemleri (Battery AA, Earphone 3.5mm New Style) teslim adedinin en fazla
               %3'ü (yukarı yuvarlanır), diğer tüm kalemler teslim adediyle aynı.

Form, sitenin kendi "Edit Order" penceresi üzerinden doldurulur; böylece sitenin tüm
doğrulamaları aynen çalışır. Beklenmeyen bir onay penceresi çıkarsa işlem KAYDEDİLMEZ.

Ortam değişkenleri: QV_ACTION, QV_ORDER_ID, QV_ORDER_DATE (YYYY-AA-GG, siparişin o takvimdeki
günü), DRY_RUN=1 (formu doldurur ama kaydetmez) ve scraper.py ile aynı giriş değişkenleri.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import date
from typing import Any

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


@dataclass(frozen=True)
class ActionSpec:
    """Bir işlemin Quietvox karşılığı."""

    status_value: str  # Order[status] seçeneğinin değeri
    status_name: str  # feed'deki durum adı (doğrulama için)
    kind: str  # hangi takvimde aranır: "delivery" | "return"
    calendar_path: str
    label: str  # bildirim metni


ACTIONS = {
    # Form değerleri canlı sitede doğrulandı: 2 = Delivered, 6 = Returned (5 = Confirmed'dır!)
    "delivered": ActionSpec("2", "delivered", "delivery", "/calendar/deliveryCalendar.html", "Delivered"),
    "returned": ActionSpec("6", "returned", "return", "/calendar/returnCalendar.html", "Returned"),
}

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
  const errors = '#order-form .errorSummary, #order-form .help-inline.error, #order-form .alert-error, .alert-error';
  for (const node of document.querySelectorAll(errors)) {
    if (visible(node) && node.textContent.trim()) texts.push(node.textContent.trim().replace(/\s+/g, ' '));
  }
  const extra = [...document.querySelectorAll('.modal.in')].filter((modal) => modal.id !== 'dialog_large');
  for (const modal of extra) texts.push('Onay penceresi: ' + modal.textContent.trim().replace(/\s+/g, ' '));
  const open = !!document.querySelector('#dialog_large.in');
  return {open, texts: texts.slice(0, 5).map((text) => text.slice(0, 300))};
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


async def open_edit_dialog(page: Any, settings: scraper.Settings, spec: ActionSpec,
                           order_id: str, day: date) -> None:
    """Takvimde siparişi bulur ve "Edit Order" penceresini açar; doğru sipariş olduğunu doğrular."""
    await scraper.goto(page, settings.base_url + spec.calendar_path, settings)
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
    """Kaydeder; pencere kapanırsa boş string, sorun varsa açıklamasını döner."""
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
        if state["texts"]:
            return "; ".join(state["texts"])
        if not state["open"]:
            return ""
    return "kaydetme zaman aşımına uğradı"


async def confirm_status(page: Any, settings: scraper.Settings, spec: ActionSpec,
                         order_id: str, day: date) -> bool:
    """Takvim feed'inden durumun gerçekten değiştiğini doğrular."""
    url = scraper.feed_url_for(settings.base_url + spec.calendar_path, spec.kind, day, 1)
    response = await page.context.request.get(url, headers={"X-Requested-With": "XMLHttpRequest"})
    items = scraper.parse_feed_items(json.loads(await response.text()), day, 1) or []
    return any(item["id"] == order_id and item["status"] == spec.status_name for item in items)


async def apply_action(page: Any, settings: scraper.Settings, spec: ActionSpec,
                       order_id: str, day: date, dry_run: bool) -> str:
    """İşlemi uygular; başarılıysa boş string, değilse hata açıklaması döner."""
    dialogs: list[str] = []

    async def on_dialog(dialog: Any) -> None:
        dialogs.append(dialog.message)
        await dialog.dismiss()  # tanımadığımız hiçbir soruya "evet" denmez

    page.on("dialog", on_dialog)
    await open_edit_dialog(page, settings, spec, order_id, day)
    changed = await set_status(page, spec)
    if spec.status_name == "delivered":
        await page.check("#Order_is_self_delivery_1")
    await page.check("#Order_is_self_return_1")
    if spec.status_name == "returned":
        for line in await fill_return_counts(page):
            log.info("İade adedi: %s", line)
    if not changed:
        log.info("Sipariş zaten '%s' durumunda; yalnızca diğer alanlar güncelleniyor.", spec.label)
    if dry_run:
        log.info("DRY_RUN: form dolduruldu, KAYDEDİLMEDİ.")
        return ""
    problem = await save_and_check(page, dialogs)
    if problem:
        return problem
    if not await confirm_status(page, settings, spec, order_id, day):
        return "kaydedildi görünüyor ama takvimde durum değişmedi"
    return ""


async def run(settings: scraper.Settings, spec: ActionSpec, order_id: str, day: date, dry_run: bool) -> int:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=settings.headless)
        try:
            context = await scraper.new_context(browser, settings)
            page = await context.new_page()
            if not await scraper.ensure_login(context, page, settings):
                scraper.send_ntfy(settings, scraper.LOGIN_FAILED_MESSAGE, priority="high")
                return EXIT_LOGIN
            try:
                problem = await apply_action(page, settings, spec, order_id, day, dry_run)
            except Exception as exc:
                problem = str(exc) or type(exc).__name__
        finally:
            await browser.close()
    if problem:
        log.error("Sipariş %s '%s' yapılamadı: %s", order_id, spec.label, problem)
        # İş akışı geçici hatalarda yeniden dener; bildirim yalnızca son denemede gider
        if (os.environ.get("QV_NOTIFY_FAILURE") or "1").strip() != "0":
            scraper.send_ntfy(settings, f"❌ Sipariş {order_id} {spec.label} yapılamadı: {problem[:200]}",
                              priority="high")
        return EXIT_FAILED
    if not dry_run:
        log.info("Sipariş %s '%s' yapıldı.", order_id, spec.label)
        scraper.send_ntfy(settings, f"✅ Sipariş {order_id} Quietvox'ta {spec.label} yapıldı.")
    return EXIT_OK


def main() -> int:
    settings = scraper.load_settings()
    scraper.configure_logging(settings)
    if not (settings.user and settings.password):
        log.error("QV_USER ve QV_PASS tanımlı olmalı.")
        return EXIT_CONFIG
    request = read_request(dict(os.environ))
    if isinstance(request, str):
        log.error(request)
        return EXIT_CONFIG
    spec, order_id, day = request
    dry_run = (os.environ.get("DRY_RUN") or "").strip().lower() in ("1", "true", "yes")
    return asyncio.run(run(settings, spec, order_id, day, dry_run))


if __name__ == "__main__":
    sys.exit(main())
