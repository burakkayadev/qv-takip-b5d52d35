"""Kapı 4 ve Kapı 5: uygulamanın gerçek Chromium ile denetimi (canlı ya da yerel adres).

Kapı 4 — veri ve rozet doğruluğu (1280x800 masaüstü + 390x844 iPhone, PIN girişiyle):
  * data.json önbellek kırıcıyla (?t=) ağdan istenir; çevrimdışı kopya kabul edilmez.
  * Üst bilgi: veri ≤90 dk ise "Güncel:", değilse "⚠️ Veri Eski (Son çekim: ...)".
  * Yeşil "Sistemde Onaylandı (Doğrulandı)" rozeti YALNIZCA taze veride ve kartın portal durumu
    hedefe ulaştıysa görünür; eski veride hiç görünmez.
  * Yan menü sayaçları (Önceden Alınanlar ve Geciken dahil) data.json'dan hesaplanan değerlere
    eşittir ve sekme/filtre değişince değişmez.
Kapı 5 — sıfır gerileme: 0 konsol/JS/ağ hatası, dinamik otel/acente filtreleri, Otele Göre
  Grupla, 30 günlük radar, tek sayfa Küçük/Büyük PDF, mobilde taşma yok, sw.js / version.json /
  index.html sürüm tutarlılığı, satır içi JS sözdizimi.

Denetim hiçbir işlem başlatmaz: tarayıcıda GitHub anahtarı yoktur (Quietvox'a yazamaz).
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Istanbul")
STALE_MINUTES = 90
INACTIVE = frozenset({"cancelled", "lost", "duplicated"})
RANK = {"reserved": 1, "confirmed": 2, "delivered": 5, "returned": 6}
CDN_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com", "cdnjs.cloudflare.com")
DESKTOP = {"viewport": {"width": 1280, "height": 800}}
IPHONE = {
    "viewport": {"width": 390, "height": 844}, "device_scale_factor": 3, "is_mobile": True, "has_touch": True,
    "user_agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                   "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"),
}
PRINT_MODES = (("waybill", "Küçük"), ("form", "Büyük"))
PIN_SHA_RE = re.compile(r"PIN_DEFAULT\s*=\s*\{\s*sha:\s*'([a-f0-9]{64})'")

JS_COUNTERS = """() => Object.fromEntries(['deliveries', 'returns', 'archive', 'overdue'].map(
  (t) => [t, ((document.getElementById('nav-n-' + t) || {}).textContent || '').trim()]))"""

JS_CARDS = """() => [...document.querySelectorAll('#list .card')].map((c) => ({
  id: c.dataset.qvId || '', status: c.dataset.status || '', kind: c.dataset.kind || '',
  verified: /Doğrulandı/.test(c.textContent || ''),
}))"""

JS_SHOW = """([tab, flt]) => {
  if (flt === 'overdue') { document.getElementById('nav-overdue').click(); return; }
  const nav = document.getElementById('nav-' + tab);
  if (nav) nav.click();
  const f = document.querySelector('.filter[data-filter="' + flt + '"]');
  if (f) f.click();
}"""

JS_REGRESSION = """() => {
  const radar = document.getElementById('prep-radar');
  const rect = radar ? radar.getBoundingClientRect() : {height: 0};
  const group = document.getElementById('btn-group-hotel');
  const parts = [];
  for (const sel of ['header.topbar', '#app', 'nav#drawer', '#bulkbar']) {
    const n = document.querySelector(sel);
    if (n && n.innerText) parts.push(n.innerText);
  }
  return {
    hotels: document.querySelectorAll('#select-hotel option').length - 1,
    agencies: document.querySelectorAll('#select-agency option').length - 1,
    groupText: group ? group.textContent.trim() : '',
    radarVisible: !!radar && !radar.hidden && rect.height > 0,
    uiText: parts.join('\\n'),
    overflow: document.documentElement.scrollWidth > window.innerWidth,
  };
}"""


# --- data.json'dan beklenen değerler (index.html kurallarının birebir karşılığı) ---------------

def _s(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _orders(document: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [order for order in document.get(key) or [] if isinstance(order, dict)]


def _reached(status: str, target: str) -> bool:
    return RANK.get(status, 0) >= RANK[target]


def expected_counts(document: dict[str, Any], today: str) -> dict[str, int]:
    """Yan menü sayaçlarının data.json'dan hesaplanan değerleri (tarayıcıda yerel işaret yokken)."""
    deliveries, returns = _orders(document, "deliveries"), _orders(document, "returns")

    def upcoming_pending(orders: list[dict[str, Any]], target: str) -> int:
        return sum(1 for o in orders if _s(o.get("date")) >= today and _s(o.get("status")) not in INACTIVE
                   and not _reached(_s(o.get("status")), target))

    done = [o for o in returns if _s(o.get("status")) == "returned"]
    done += [o for o in deliveries if _reached(_s(o.get("status")), "delivered")]
    keys: set[str] = set()
    for order in done:
        waybill = order.get("waybill") if isinstance(order.get("waybill"), dict) else {}
        keys.add(_s(order.get("id")) or _s(waybill.get("qv_id")) or _s(order.get("date")) + _s(order.get("title")))
    return {
        "deliveries": upcoming_pending(deliveries, "delivered"),
        "returns": upcoming_pending(returns, "returned"),
        "archive": len(keys),
        "overdue": sum(1 for o in returns if _s(o.get("date")) < today and _s(o.get("status")) == "delivered"),
    }


def expected_filters(document: dict[str, Any]) -> tuple[int, int]:
    """Otel ve tur/acente filtrelerinde olması gereken (tekrarsız) seçenek sayıları."""
    orders = _orders(document, "deliveries") + _orders(document, "returns")
    hotels = {_s(o.get("location_name")) or _s(o.get("title")) for o in orders} - {""}
    agencies = set()
    for order in orders:
        waybill = order.get("waybill") if isinstance(order.get("waybill"), dict) else {}
        form = order.get("dr_form") if isinstance(order.get("dr_form"), dict) else {}
        agencies.add(_s(waybill.get("client")) or _s(form.get("customer")))
    return len(hotels), len(agencies - {""})


def data_age_minutes(document: dict[str, Any], now: datetime | None = None) -> float | None:
    try:
        generated = datetime.fromisoformat(_s(document.get("generated_at")).replace("Z", "+00:00"))
    except ValueError:
        return None
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - generated).total_seconds() / 60


def badge_problems(cards: list[dict[str, Any]], stale: bool, view: str) -> list[str]:
    """Yeşil "Doğrulandı" rozeti yalnızca taze veride ve gerçek durumda olmalı (ve orada eksiksiz)."""
    problems = []
    for card in cards:
        target = "returned" if card["kind"] == "return" else "delivered"
        reached = _reached(card["status"], target)
        if card["verified"] and stale:
            problems.append(f"[{view}] {card['id']}: veri eskiyken 'Doğrulandı' rozeti")
        elif card["verified"] and not reached:
            problems.append(f"[{view}] {card['id']}: portal durumu '{card['status']}' iken 'Doğrulandı' rozeti")
        elif view == "archive" and not stale and reached and not card["verified"]:
            problems.append(f"[{view}] {card['id']}: '{card['status']}' taze veride ama 'Doğrulandı' rozeti yok")
    return problems


# --- HTTP ve statik denetimler --------------------------------------------------------------------

def http_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "quietvox-healthcheck"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def version_problems(base_url: str) -> tuple[list[str], dict[str, Any], str]:
    """sw.js VERSION, version.json build ve index.html CURRENT_BUILD aynı mı? (+ yayınlanan HTML)"""
    stamp = int(time.time() * 1000)
    html = http_text(f"{base_url}index.html?t={stamp}")
    sw_text = http_text(f"{base_url}sw.js?t={stamp}")
    version = json.loads(http_text(f"{base_url}version.json?t={stamp}"))
    m_sw = re.search(r"const VERSION = 'b(\d+)'", sw_text)
    m_ix = re.search(r"var CURRENT_BUILD = (\d+);", html)
    builds = {"sw.js": int(m_sw.group(1)) if m_sw else None, "version.json": version.get("build"),
              "index.html": int(m_ix.group(1)) if m_ix else None}
    consistent = None not in builds.values() and len(set(builds.values())) == 1
    return ([] if consistent else [f"sürüm numaraları tutarsız: {builds}"]), builds, html


def js_syntax_problems(html: str) -> list[str]:
    """Yayınlanan index.html'deki satır içi betikler node --check ile derlenir."""
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.S)
    if not blocks:
        return ["index.html içinde satır içi betik bulunamadı"]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "inline.js"
        path.write_text("\n;\n".join(blocks), encoding="utf-8")
        try:
            result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, timeout=60)
        except FileNotFoundError:
            return ["node bulunamadı; JS sözdizimi denetlenemedi"]
    return [] if result.returncode == 0 else [f"JS sözdizimi hatası: {result.stderr.strip()[:300]}"]


# --- Tarayıcı adımları -----------------------------------------------------------------------------

def _watched(url: str, base_url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return url.startswith(base_url) or host in CDN_HOSTS


async def _fresh_route(route: Any) -> None:
    """YALNIZCA yerel denemede (--simulate-fresh): data.json'un generated_at'ini şimdiye çeker."""
    response = await route.fetch()
    raw = await response.json()
    raw["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    await route.fulfill(response=response, json=raw)


async def unlock(page: Any, pin: str) -> str:
    """PIN kilidini açar: QV_APP_PIN varsa tuş takımından, yoksa yayınlanan özetle (PIN'i bilmeden)."""
    if await page.locator("#pinLock").count() == 0:
        return "PIN kilidi yok"
    if pin:
        for digit in pin:
            await page.locator(f"#pinLock button.pl-key:has-text('{digit}')").click()
        await page.wait_for_selector("#pinLock", state="detached", timeout=10000)
        return "PIN tuş takımıyla girildi"
    match = PIN_SHA_RE.search(await page.content())
    if not match:
        raise RuntimeError("PIN özeti sayfada bulunamadı")
    await page.evaluate("(sha) => localStorage.setItem('qv.pin.ok.v1', sha)", match.group(1))
    await page.reload(wait_until="networkidle", timeout=45000)
    return "PIN oturum özetiyle açıldı (QV_APP_PIN tanımlı değil)"


async def capture_fresh_data(page: Any) -> tuple[dict[str, Any], list[str]]:
    """"Yenile" ile tetiklenen data.json isteğini yakalar; önbelleksiz ve ağdan mı geldi?"""
    problems = []
    async with page.expect_response(lambda r: "/data.json" in r.url, timeout=30000) as info:
        await page.evaluate("() => document.getElementById('btn-refresh').click()")
    response = await info.value
    if not re.search(r"[?&]t=\d{10,}", response.url):
        problems.append(f"data.json önbellek kırıcı (?t=) olmadan istendi: {response.url}")
    if response.status != 200:
        problems.append(f"data.json HTTP {response.status}")
    if (await response.all_headers()).get("x-qv-offline"):
        problems.append("data.json çevrimdışı kopyadan geldi")
    raw = await response.json()
    await page.wait_for_timeout(900)
    return raw, problems


async def show(page: Any, tab: str, flt: str = "all") -> None:
    await page.evaluate(JS_SHOW, [tab, flt])
    await page.wait_for_timeout(400)


async def counter_problems(page: Any, expected: dict[str, int]) -> list[str]:
    """Sayaçlar her görünümde (Verilecekler, Önceden Alınanlar, Geciken, Bugün) beklenene eşit mi?"""
    problems = []
    for tab, flt in (("deliveries", "all"), ("archive", "all"), ("returns", "overdue"), ("returns", "today")):
        if flt == "overdue" and expected["overdue"] == 0:
            continue
        await show(page, tab, flt)
        raw = await page.evaluate(JS_COUNTERS)
        got = {key: int(value) if value.isdigit() else -1 for key, value in raw.items()}
        if got != expected:
            problems.append(f"[{tab}/{flt}] sayaçlar {got} ≠ beklenen {expected}")
    return problems


async def print_pages(page: Any, mode: str) -> tuple[int, int, int]:
    """İlk kartın tekli yazdırması: (print çağrısı artışı, sayfa öğesi, PDF sayfa sayısı)."""
    await show(page, "deliveries", "all")
    card = page.locator("#list .card").first
    await card.locator("details.more").evaluate("(el) => { el.open = true; }")
    before = await page.evaluate("() => window.__hcPrintCount")
    await card.locator("button.qbtn:has-text('Yazdır')").click()
    await page.wait_for_selector("#dlg[open]", timeout=5000)
    await page.locator(f'#dlg [data-print-mode="{mode}"] .btn-print').click()
    await page.wait_for_timeout(500)
    info = await page.evaluate(
        "() => ({n: window.__hcPrintCount, sheets: document.querySelectorAll('#print-sheets .sheet-wrap').length})")
    pdf = await page.pdf(format="A4", print_background=False, prefer_css_page_size=True)
    await page.evaluate("() => { const d = document.getElementById('dlg'); if (d && d.open) d.close(); }")
    return info["n"] - before, info["sheets"], len(re.findall(rb"/Type\s*/Page\b(?!s)", pdf))


async def regression_problems(page: Any, document: dict[str, Any], label: str, mobile: bool) -> tuple[list[str], str]:
    """Kapı 5'in sayfa içi maddeleri; (sorunlar, not) döner."""
    problems = []
    await show(page, "deliveries", "all")
    state = await page.evaluate(JS_REGRESSION)
    hotels, agencies = expected_filters(document)
    if (state["hotels"], state["agencies"]) != (hotels, agencies):
        problems.append(f"filtreler otel={state['hotels']}/{hotels}, acente={state['agencies']}/{agencies}")
    if "Otele Göre Grupla" not in state["groupText"]:
        problems.append("'Otele Göre Grupla' düğmesi yok")
    else:
        pressed = await page.evaluate("""() => { const b = document.getElementById('btn-group-hotel'); b.click();
          const on = b.getAttribute('aria-pressed'); b.click(); return [on, b.getAttribute('aria-pressed')]; }""")
        if pressed != ["true", "false"]:
            problems.append(f"'Otele Göre Grupla' aç/kapa çalışmıyor ({pressed})")
    if not state["radarVisible"]:
        problems.append("30 günlük yoğunluk radarı (#prep-radar) görünmüyor")
    if re.search(r"ho[şs]\s*geldin\s*mehmet", state["uiText"], re.I):
        problems.append("'Hoşgeldin Mehmet' metni geri gelmiş")
    if re.search(r"\bwaybill\b|delivery\s*[&and]+\s*return", state["uiText"], re.I):
        problems.append("arayüzde İngilizce 'Waybill / Delivery & Return' kalıntısı")
    note = f"otel {hotels}, tur/acente {agencies}"
    if mobile and state["overflow"]:
        problems.append("mobilde yatay taşma")
    if not mobile:
        for mode, name in PRINT_MODES:
            calls, sheets, pages = await print_pages(page, mode)
            if (calls, sheets, pages) != (1, 1, 1):
                problems.append(f"tekli {name} yazdırma 1 sayfa değil (print={calls}, sayfa öğesi={sheets}, PDF={pages})")
        note += ", tekli Küçük=1 sayfa, Büyük=1 sayfa"
    return [f"[{label}] {p}" for p in problems], note


async def data_problems(page: Any, document: dict[str, Any], label: str) -> list[str]:
    """Kapı 4: üst bilgi (Güncel / Veri Eski), rozetler ve sayaçlar."""
    problems = []
    age = data_age_minutes(document)
    stale = age is None or age > STALE_MINUTES
    updated = (await page.inner_text("#updated")).strip()
    expected_head = "⚠️ Veri Eski (Son çekim:" if stale else "Güncel:"
    if not updated.startswith(expected_head):
        shown_age = "?" if age is None else round(age)
        problems.append(f"üst bilgi '{updated}' (veri yaşı {shown_age} dk, beklenen '{expected_head}')")
    cards_checked = 0
    for view in ("archive", "returns", "deliveries"):
        await show(page, view, "all")
        cards = await page.evaluate(JS_CARDS)
        cards_checked += len(cards)
        problems += badge_problems(cards, stale, view)
    today = datetime.now(TZ).date().isoformat()
    problems += await counter_problems(page, expected_counts(document, today))
    if cards_checked == 0:
        problems.append("hiç sipariş kartı bulunamadı")
    return [f"[{label}] {p}" for p in problems]


async def audit_viewport(browser: Any, base_url: str, label: str, device: dict[str, Any],
                         pin: str, simulate_fresh: bool) -> dict[str, Any]:
    """Tek ekran boyutunda Kapı 4 ve Kapı 5 maddelerini çalıştırır."""
    result: dict[str, Any] = {"gate4": [], "gate5": [], "notes": []}
    context = await browser.new_context(**device, service_workers="block" if simulate_fresh else "allow")
    page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda err: errors.append(f"JS hatası: {err}"))
    page.on("console", lambda msg: errors.append(f"console.error: {msg.text}") if msg.type == "error" else None)
    page.on("requestfailed", lambda req: errors.append(f"ağ hatası: {req.url[:120]} ({req.failure})")
            if _watched(req.url, base_url) and "ERR_ABORTED" not in str(req.failure) else None)
    page.on("response", lambda res: errors.append(f"HTTP {res.status}: {res.url[:120]}")
            if res.status >= 400 and _watched(res.url, base_url) else None)
    await page.add_init_script("window.__hcPrintCount = 0; window.print = function () { window.__hcPrintCount++; };")
    if simulate_fresh:
        await context.route(re.compile(r".*/data\.json(\?.*)?$"), _fresh_route)
    try:
        await page.goto(f"{base_url}?v={int(time.time())}", wait_until="networkidle", timeout=45000)
        result["notes"].append(await unlock(page, pin))
        await page.wait_for_selector("#day-h", timeout=20000)
        document, fetch_problems = await capture_fresh_data(page)
        result["gate4"] += [f"[{label}] {p}" for p in fetch_problems]
        result["gate4"] += await data_problems(page, document, label)
        regression, note = await regression_problems(page, document, label, "is_mobile" in device)
        result["gate5"] += regression
        result["notes"].append(note)
    except Exception as exc:
        result["gate4"].append(f"[{label}] denetim yarıda kaldı: {type(exc).__name__}: {str(exc)[:200]}")
    finally:
        await context.close()
    result["gate5"] += [f"[{label}] {e}" for e in errors[:5]]
    return result


async def run_ui_gates(base_url: str, pin: str = "", simulate_fresh: bool = False) -> tuple[dict, dict]:
    """Kapı 4 ve Kapı 5'i çalıştırır; ({name, ok, detail}, {name, ok, detail}) döner."""
    from playwright.async_api import async_playwright

    base_url = base_url if base_url.endswith("/") else base_url + "/"
    gate4: list[str] = []
    gate5: list[str] = []
    notes: list[str] = []
    try:
        version_issues, builds, html = version_problems(base_url)
        gate5 += version_issues + js_syntax_problems(html)
        notes.append(f"sürüm {builds.get('version.json')}")
    except Exception as exc:
        gate5.append(f"sürüm/sözdizimi denetimi yapılamadı: {type(exc).__name__}: {exc}")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for label, device in (("Masaüstü 1280x800", DESKTOP), ("iPhone 390x844", IPHONE)):
                result = await audit_viewport(browser, base_url, label, device, pin, simulate_fresh)
                gate4 += result["gate4"]
                gate5 += result["gate5"]
                notes += result["notes"]
        finally:
            await browser.close()
    unique_notes = list(dict.fromkeys(notes))
    simulated = " (YEREL: veri tazeliği benzetildi)" if simulate_fresh else ""
    return (
        {"name": "Kapı 4 · Canlı veri, rozet ve sayaçlar", "ok": not gate4,
         "detail": " | ".join(gate4[:12]) if gate4 else "masaüstü + iPhone: ?t= ile ağdan veri, rozetler gerçek "
                   f"durumla, sayaçlar tüm görünümlerde doğru{simulated}"},
        {"name": "Kapı 5 · Sıfır gerileme", "ok": not gate5,
         "detail": " | ".join(gate5[:12]) if gate5 else "0 konsol/ağ hatası; " + "; ".join(unique_notes)},
    )
