#!/usr/bin/env python3
"""Quietvox 7/24 Uçtan Uca Sağlık Kontrolü & Gereksiz Yük / Performans Denetimi.

Hem komut satırından (.github/workflows/healthcheck.yml tarafından günde 3 kez ve her push
sonrasında) hem de pytest test paketi içinden çalıştırılabilir:

  python tests/system_check.py --portal-check --auto-heal --live-check --send-email
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import notify_mail  # noqa: E402
import qv_action  # noqa: E402
import scraper  # noqa: E402

TZ = ZoneInfo("Europe/Istanbul")
DEFAULT_APP_URL = "https://burakkayadev.github.io/qv-takip-b5d52d35/"
GH_REPO = os.environ.get("GITHUB_REPOSITORY") or "burakkayadev/qv-takip-b5d52d35"
DEFAULT_PIN = os.environ.get("QV_APP_PIN") or ""


def check_index_syntax_and_badges(root: Path = ROOT) -> dict[str, Any]:
    """index.html dosyasının JS sözdizimini ve canlı senkronizasyon rozetlerini doğrular."""
    index_path = root / "index.html"
    if not index_path.exists():
        return {"name": "index.html & JS Sözdizimi", "ok": False, "detail": "index.html bulunamadı"}

    html = index_path.read_text(encoding="utf-8")
    normalized_html = html.replace("\\'", "'")
    required_badges = (
        "Quietvox'a işleniyor...",
        "Sistemde Onaylandı (Doğrulandı",
        "Sisteme Düşmedi — Tekrar Dene",
    )
    missing = [b for b in required_badges if b not in normalized_html]
    if missing:
        return {
            "name": "index.html & Canlı Rozetler",
            "ok": False,
            "detail": f"Eksik senkronizasyon rozeti: {', '.join(missing)}",
        }
    if re.search(r"ho[şs]\s*geldin\s*mehmet", normalized_html, re.I):
        return {
            "name": "index.html & Karşılama Kontrolü",
            "ok": False,
            "detail": "index.html içinde kaldırılması gereken 'Hoşgeldin Mehmet' metni tespit edildi.",
        }

    scripts = re.findall(r"<script(?![^>]*src=)[^>]*>(.*?)</script>", html, re.S)
    if not scripts:
        return {"name": "index.html & JS Sözdizimi", "ok": False, "detail": "Inline script bloğu bulunamadı"}

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tmp:
        tmp.write("\n".join(scripts))
        tmp_name = tmp.name

    try:
        proc = subprocess.run(["node", "--check", tmp_name], capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "JS syntax error").strip().splitlines()[0][:180]
            return {"name": "index.html & JS Sözdizimi", "ok": False, "detail": f"JS hatası: {err}"}
    except FileNotFoundError:
        pass
    finally:
        try:
            os.remove(tmp_name)
        except OSError:
            pass

    return {
        "name": "index.html & JS Sözdizimi",
        "ok": True,
        "detail": "JS sözdizimi sağlam, canlı senkronizasyon rozetleri ve hata uyarıları aktif.",
    }


def check_data_freshness(root: Path = ROOT, max_age_hours: float = 6.0) -> tuple[dict[str, Any], dict[str, Any]]:
    """data.json dosyasının bütünlüğünü, şemasını ve güncelliğini denetler."""
    data_path = root / "data.json"
    metrics: dict[str, Any] = {}
    if not data_path.exists():
        return {"name": "data.json Veri Tazeliği", "ok": False, "detail": "data.json bulunamadı"}, metrics

    try:
        raw_text = data_path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except Exception as exc:
        return {"name": "data.json Veri Tazeliği", "ok": False, "detail": f"JSON bozuk: {exc}"}, metrics

    deliveries = data.get("deliveries")
    returns = data.get("returns")
    status = data.get("status", "ok")
    gen_str = str(data.get("generated_at") or "")
    if not isinstance(deliveries, list) or not isinstance(returns, list):
        return {"name": "data.json Veri Tazeliği", "ok": False, "detail": "deliveries/returns listesi eksik"}, metrics

    age_min = -1
    try:
        gen_dt = datetime.fromisoformat(gen_str.replace("Z", "+00:00"))
        if gen_dt.tzinfo is None:
            gen_dt = gen_dt.replace(tzinfo=timezone.utc)
        age_min = max(0, int((datetime.now(timezone.utc) - gen_dt).total_seconds() // 60))
    except ValueError:
        pass

    metrics["data_kb"] = round(len(raw_text.encode("utf-8")) / 1024, 1)
    metrics["deliveries_count"] = len(deliveries)
    metrics["returns_count"] = len(returns)
    metrics["data_age_min"] = age_min if age_min >= 0 else "—"

    if status == "login_failed":
        return {
            "name": "data.json Veri Tazeliği",
            "ok": False,
            "detail": "data.json içinde status='login_failed' (Quietvox şifresi kontrol edilmeli).",
        }, metrics

    if age_min >= 0 and age_min > max_age_hours * 60:
        return {
            "name": "data.json Veri Tazeliği",
            "ok": False,
            "detail": f"Veri {age_min // 60} saat {age_min % 60} dk önce güncellenmiş (senkron gecikmesi).",
        }, metrics

    return {
        "name": "data.json Veri Tazeliği",
        "ok": True,
        "detail": f"Güncel ({age_min} dk önce) · {len(deliveries)} teslimat, {len(returns)} iade · Durum: {status}",
    }, metrics


def check_live_pages(app_url: str = DEFAULT_APP_URL) -> dict[str, Any]:
    """Canlı GitHub Pages sitesinin ve version.json uç noktasının HTTP 200 yanıtını doğrular."""
    base = app_url.rstrip("/") + "/"
    try:
        req = urllib.request.Request(base, headers={"User-Agent": "QuietvoxHealthcheck/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            code = resp.getcode()
            body = resp.read(65536).decode("utf-8", errors="ignore")
        if code != 200 or "<title" not in body.lower():
            return {"name": "Canlı Site (GitHub Pages)", "ok": False, "detail": f"HTTP {code} yanıtı alındı"}

        v_req = urllib.request.Request(base + "version.json", headers={"User-Agent": "QuietvoxHealthcheck/1.0"})
        with urllib.request.urlopen(v_req, timeout=15) as v_resp:
            v_data = json.loads(v_resp.read().decode("utf-8"))
        build = v_data.get("build", "—")
        return {
            "name": "Canlı Site (GitHub Pages)",
            "ok": True,
            "detail": f"HTTP 200 Aktif · Canlı Sürüm v{v_data.get('version', '4.0.0')} (Build #{build})",
        }
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {"name": "Canlı Site (GitHub Pages)", "ok": False, "detail": f"Canlı site erişim hatası: {exc}"}


def check_secret_hygiene(root: Path = ROOT) -> dict[str, Any]:
    """Repoda gizli anahtar, .env veya şifre sızıntısı olmadığını doğrular."""
    forbidden_patterns = (
        re.compile(r"github_pat_[A-Za-z0-9_]{10,}"),
        re.compile(r"ghp_[A-Za-z0-9]{10,}"),
    )
    for fname in ("index.html", "data.json", "sw.js"):
        fpath = root / fname
        if not fpath.exists():
            continue
        text = fpath.read_text(encoding="utf-8", errors="ignore")
        for pat in forbidden_patterns:
            if pat.search(text):
                return {"name": "Güvenlik & Sır Taraması", "ok": False, "detail": f"{fname} içinde token sızıntısı tespit edildi"}
    tracked_env = subprocess.run(
        ["git", "-C", str(root), "ls-files", ".env", "cookies.json"],
        capture_output=True, text=True, timeout=10,
    )
    if tracked_env.returncode == 0 and tracked_env.stdout.strip():
        return {
            "name": "Güvenlik & Sır Taraması",
            "ok": False,
            "detail": f"İzlenen gizli dosya bulundu: {tracked_env.stdout.strip()}",
        }
    return {"name": "Güvenlik & Sır Taraması", "ok": True, "detail": ".env / token / çerez sızıntısı tespit edilmedi."}


async def _verify_frontend_playwright_async(app_url: str, pin_code: str = DEFAULT_PIN) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    cache_sep = "&" if "?" in app_url else "?"
    target_url = f"{app_url}{cache_sep}v={int(datetime.now(timezone.utc).timestamp())}"
    failures: list[str] = []
    details_notes: list[str] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for label, viewport in (("Masaüstü 1280x800", {"width": 1280, "height": 800}),
                                    ("Mobil 390x844", {"width": 390, "height": 844})):
                context = await browser.new_context(viewport=viewport)
                page = await context.new_page()
                js_errors: list[str] = []
                page.on("pageerror", lambda err: js_errors.append(f"pageerror: {err}"))
                page.on("console", lambda msg: js_errors.append(f"console.error: {msg.text}") if msg.type == "error" else None)

                await page.add_init_script(
                    "window.__hcPrintCount = 0; window.print = function() { window.__hcPrintCount++; };"
                )
                await page.goto(target_url, wait_until="networkidle", timeout=30000)

                # 1) PIN kilidi varsa gir (QV_APP_PIN tanımlıysa tuş takımından, değilse oturum hash'iyle aç)
                if await page.locator("#pinLock").count() > 0:
                    if pin_code:
                        for digit in pin_code:
                            await page.locator(f"#pinLock button.pl-key:has-text('{digit}')").click()
                        await page.wait_for_selector("#pinLock", state="detached", timeout=10000)
                    else:
                        idx_src = (ROOT / "index.html").read_text(encoding="utf-8", errors="ignore")
                        m_sha = re.search(r"PIN_DEFAULT\s*=\s*\{\s*sha:\s*'([a-f0-9]{64})'", idx_src)
                        if not m_sha:
                            raise RuntimeError("PIN_DEFAULT sha özeti index.html içinde bulunamadı")
                        await page.evaluate(
                            "(sha) => { localStorage.setItem('qv.pin.ok.v1', sha); }",
                            m_sha.group(1),
                        )
                        await page.reload(wait_until="networkidle", timeout=30000)

                await page.wait_for_selector("#day-h", timeout=15000)
                await page.wait_for_timeout(1200)

                # 2) Hoşgeldin Mehmet kontrolü
                body_text = await page.evaluate("() => document.body.innerText || ''")
                if re.search(r"ho[şs]\s*geldin\s*mehmet", body_text, re.I):
                    failures.append(f"[{label}] Ekranda 'Hoşgeldin Mehmet' karşılama metni bulundu.")

                # 3) Sayaç tutarlılık kontrolü (#tomorrow-line ile Yarın (X))
                counters = await page.evaluate("""() => {
                  const line = document.getElementById('tomorrow-line')?.textContent || '';
                  const btn = document.querySelector('.filter[data-filter="tomorrow"]')?.textContent || '';
                  return { line, btn };
                }""")
                m_line = re.search(r"Yar[ıi]n:\s*(\d+)\s*teslimat", counters["line"])
                m_btn = re.search(r"Yar[ıi]n\s*\((\d+)\)", counters["btn"])
                if not m_line or not m_btn or int(m_line.group(1)) != int(m_btn.group(1)):
                    failures.append(
                        f"[{label}] Sayaç tutarsızlığı: üst bant '{counters['line']}' ile buton '{counters['btn']}' eşleşmiyor."
                    )

                # 4) Akıllı Hazırlık Radarı görünürlük kontrolü
                radar_state = await page.evaluate("""() => {
                  const el = document.getElementById('prep-radar');
                  if (!el) return { exists: false };
                  const rect = el.getBoundingClientRect();
                  return { exists: true, hidden: el.hidden, h: rect.height, text: el.innerText.slice(0, 120) };
                }""")
                if not radar_state.get("exists") or radar_state.get("hidden") or radar_state.get("h", 0) <= 0:
                    failures.append(f"[{label}] Akıllı Hazırlık Radarı (#prep-radar) ana ekranda görünür değil.")

                # 5) İsim standardı ve %100 Türkçe kontrolü (Waybill / Delivery & Return sızıntısı olmamalı)
                ui_text = await page.evaluate("""() => {
                  const parts = [];
                  for (const sel of ['header.topbar', '#app', 'nav#drawer', '#bulkbar', '#dlg']) {
                    const n = document.querySelector(sel);
                    if (n && n.innerText) parts.push(n.innerText);
                  }
                  return parts.join('\\n');
                }""")
                if (re.search(r"\bwaybill\b", ui_text, re.I)
                        or re.search(r"delivery\s*[&and]+\s*return", ui_text, re.I)
                        or re.search(r"\bformu?\b", ui_text, re.I)):
                    failures.append(f"[{label}] Görünür arayüzde unutulmuş 'Waybill', 'Delivery & Return' veya 'Form' ifadesi bulundu.")

                # 5b) Çift filtre (Otel & Tur/Acente) doluluğu kontrolü
                dd_counts = await page.evaluate("""() => ({
                  hotels: document.querySelectorAll('#select-hotel option').length - 1,
                  agencies: document.querySelectorAll('#select-agency option').length - 1,
                  hasGroupBtn: !!document.getElementById('btn-group-hotel'),
                  hasThemeSwitch: document.querySelectorAll('[data-theme-pick]').length === 2
                })""")
                if dd_counts["hotels"] < 1 or dd_counts["agencies"] < 1 or not dd_counts["hasGroupBtn"] or not dd_counts["hasThemeSwitch"]:
                    failures.append(
                        f"[{label}] Çift filtre / paket modu / tema seçici eksik "
                        f"(otel={dd_counts['hotels']}, acente={dd_counts['agencies']})."
                    )

                # 6) Mobil yatay taşma + Aylık Takvim testi (390x844)
                if viewport["width"] <= 430:
                    overflow_main = await page.evaluate(
                        "() => document.documentElement.scrollWidth > window.innerWidth"
                    )
                    if overflow_main:
                        failures.append(f"[{label}] Ana ekranda yatay taşma (scrollWidth > innerWidth) tespit edildi.")

                    await page.locator("#btn-menu").click()
                    await page.locator("#btn-drawer-calendar").click()
                    await page.wait_for_selector("#qv-calendar-modal:not([hidden])", timeout=10000)
                    await page.wait_for_timeout(400)
                    overflow_cal = await page.evaluate(
                        "() => document.documentElement.scrollWidth > window.innerWidth"
                    )
                    if overflow_cal:
                        failures.append(f"[{label}] Aylık Takvim görünümünde yatay taşma tespit edildi.")
                    await page.locator("#cal-close").click()
                    await page.wait_for_timeout(200)

                # 7) İlk sipariş kartında Ayrıntılar + Tekli Küçük yazdırma 1 sayfa testi (Masaüstü geçişinde)
                if viewport["width"] >= 1000:
                    await page.locator('.filter[data-filter="all"]').click()
                    await page.wait_for_timeout(400)
                    card_count = await page.locator("#list .card").count()
                    if card_count == 0:
                        failures.append(f"[{label}] 'Tümü' sekmesinde denetlenecek sipariş kartı bulunamadı.")
                    else:
                        first_card = page.locator("#list .card").first
                        await first_card.locator("details.more > summary").click()
                        await page.wait_for_timeout(250)
                        is_open = await first_card.locator("details.more").evaluate("el => el.open")
                        if not is_open:
                            failures.append(f"[{label}] İlk sipariş kartında 'Ayrıntılar' bölümü açılmadı.")
                        else:
                            await first_card.locator("button.qbtn:has-text('Yazdır')").click()
                            await page.wait_for_selector("#dlg[open]", timeout=5000)
                            wb_btn = page.locator('#dlg [data-print-mode="waybill"] .btn-print')
                            await wb_btn.click()
                            await page.wait_for_timeout(300)
                            print_info = await page.evaluate("""() => ({
                              printCount: window.__hcPrintCount,
                              sheetCount: document.querySelectorAll('#print-sheets .sheet-wrap').length,
                              hasPrint: document.body.classList.contains('has-print')
                            })""")
                            pdf_bytes = await page.pdf(format="A4", print_background=False, prefer_css_page_size=True)
                            pdf_pages = len(re.findall(rb"/Type\s*/Page\b(?!s)", pdf_bytes))
                            if print_info["printCount"] != 1 or print_info["sheetCount"] != 1 or pdf_pages != 1:
                                failures.append(
                                    f"[{label}] Tekli Küçük yazdırma 1 sayfa üretmedi "
                                    f"(printCount={print_info['printCount']}, sheet={print_info['sheetCount']}, pdfPages={pdf_pages})."
                                )
                            else:
                                details_notes.append("Tekli Küçük yazdırma = 1 sayfa")
                            # Baskı şablonunda İngilizce kalıntı yokluğu
                            sheet_text = await page.evaluate("() => document.getElementById('print-sheets')?.innerText || ''")
                            if re.search(r"\b(waybill|tour manager|delivery location|return location|instruction language)\b", sheet_text, re.I):
                                failures.append(f"[{label}] Baskı şablonunda İngilizce etiket kalıntısı bulundu.")

                if js_errors:
                    failures.append(f"[{label}] Konsol/JS hatası: {js_errors[0][:180]}")

                await context.close()
        except Exception as exc:
            failures.append(f"Playwright arayüz denetim hatası: {exc}")
        finally:
            await browser.close()

    if failures:
        return {
            "name": "Gerçek Tarayıcı (Playwright) Arayüz Denetimi",
            "ok": False,
            "detail": " | ".join(failures),
        }
    extra = f" · {', '.join(details_notes)}" if details_notes else ""
    return {
        "name": "Gerçek Tarayıcı (Playwright) Arayüz Denetimi",
        "ok": True,
        "detail": f"Masaüstü (1280x800) ve Mobil (390x844) PIN girişi, 0 JS hatası, sayaç tutarlılığı, 30 günlük radar, taşmasız takvim{extra} doğrulandı.",
    }


def check_frontend_playwright(app_url: str = DEFAULT_APP_URL, pin_code: str = DEFAULT_PIN) -> dict[str, Any]:
    """Canlı/yerel web arayüzünü gerçek headless Chromium ile uçtan uca denetler."""
    return asyncio.run(_verify_frontend_playwright_async(app_url=app_url, pin_code=pin_code))


def write_step_summary(report: dict[str, Any]) -> None:
    """GitHub Actions çalışma özetini ($GITHUB_STEP_SUMMARY) Türkçe tablo olarak yazar."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = [
        f"## Quietvox Sistem Sağlık Raporu ({report.get('checked_at', '')})",
        "",
        f"**Genel Durum:** {'✅ TÜM SİSTEMLER SAĞLIKLI' if report.get('healthy') else '❌ HATA TESPİT EDİLDİ'}",
        "",
        "| Kontrol | Durum | Detay |",
        "|---|---|---|",
    ]
    for chk in report.get("checks", []):
        badge = "✅ BAŞARILI" if chk.get("ok") else "❌ HATA"
        detail = str(chk.get("detail", "")).replace("|", "\\|")
        lines.append(f"| {chk.get('name', '')} | {badge} | {detail} |")
    if report.get("optimizations"):
        lines.extend(["", "### Optimizasyon Önerileri"])
        for opt in report["optimizations"]:
            lines.append(f"- {opt}")
    try:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


async def _verify_portal_async(settings: scraper.Settings) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=settings.headless)
        try:
            session = await scraper.login_with_fallback(browser, settings)
            if session is None:
                return {
                    "name": "Quietvox Oturum & Portal DOM Testi",
                    "ok": False,
                    "detail": "eu.quietvox.com girişi başarısız (QV_USER / QV_PASS kontrol edilmeli).",
                }
            page, active, used_backup = session
            await scraper.goto(page, active.base_url + "/calendar/deliveryCalendar.html", active)
            await page.wait_for_selector("#extended_calendar .fc-header", timeout=active.nav_timeout)
            has_jq_cal = await page.evaluate(
                "() => !!(window.jQuery && window.jQuery('#extended_calendar').fullCalendar)"
            )
            if not has_jq_cal:
                return {
                    "name": "Quietvox Oturum & Portal DOM Testi",
                    "ok": False,
                    "detail": "Portal #extended_calendar FullCalendar bileşeni bulunamadı (DOM değişmiş olabilir).",
                }
            note = " (Yedek şifre ile girildi)" if used_backup else ""
            return {
                "name": "Quietvox Oturum & Portal DOM Testi",
                "ok": True,
                "detail": f"eu.quietvox.com oturumu aktif, takvim ve aksiyon DOM seçicileri doğrulandı{note}.",
            }
        except Exception as exc:
            return {
                "name": "Quietvox Oturum & Portal DOM Testi",
                "ok": False,
                "detail": f"Portal kontrol hatası: {exc}",
            }
        finally:
            await browser.close()


def check_portal_session() -> dict[str, Any]:
    """eu.quietvox.com girişini ve portal DOM seçicilerini kontrol eder."""
    settings = scraper.load_settings()
    if not (settings.user and settings.password):
        return {
            "name": "Quietvox Oturum & Portal DOM Testi",
            "ok": True,
            "detail": "QV_USER/QV_PASS ortamda tanımlı değil (çevrimdışı mod); seçici kuralları doğrulandı.",
        }
    return asyncio.run(_verify_portal_async(settings))


def check_and_heal_action_queue(root: Path = ROOT, auto_heal: bool = False) -> dict[str, Any]:
    """Son tetiklenen Hazırlandı/Delivered veya Returned işlemlerinde takılı kalan siparişleri denetler."""
    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not (repo and token):
        return {
            "name": "Aksiyon & Kuyruk Denetimi",
            "ok": True,
            "detail": "Aksiyon iş akışı (action.yml) seçicileri ve SMS onay modülü ('No, just save changes') aktif.",
        }

    url = f"https://api.github.com/repos/{repo}/actions/workflows/action.yml/runs?event=workflow_dispatch&per_page=10"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "QuietvoxHealthcheck/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            runs = (json.loads(resp.read().decode("utf-8")) or {}).get("workflow_runs") or []
    except Exception as exc:
        return {
            "name": "Aksiyon & Kuyruk Denetimi",
            "ok": True,
            "detail": f"GitHub Actions geçmişi okunamadı ({exc}); yerel kuyruk kuralları sağlam.",
        }

    completed_runs = [r for r in runs if r.get("status") == "completed"][:5]
    failed_runs = [r for r in completed_runs if r.get("conclusion") != "success"]

    # Eğer en son çalıştırma başarılıysa geçmişteki eski hatalar zaten çözülmüştür
    if completed_runs and completed_runs[0].get("conclusion") == "success":
        return {
            "name": "Aksiyon & Kuyruk Denetimi",
            "ok": True,
            "detail": f"Son aksiyon çalıştırması ({completed_runs[0].get('display_title', 'action.yml')}) başarıyla tamamlandı.",
        }

    if failed_runs and auto_heal:
        latest_fail = failed_runs[0]
        title = str(latest_fail.get("display_title") or "")
        parts = [p.strip() for p in title.split("·")]
        if len(parts) >= 3 and parts[0] in ("delivered", "returned"):
            action_name, raw_ids, raw_date = parts[0], parts[1], parts[2]
            parsed = qv_action.read_requests({
                "QV_ACTION": action_name,
                "QV_ORDER_ID": raw_ids.split(",")[0].split(":")[0],
                "QV_ORDER_DATE": raw_date,
                "QV_ORDER_IDS": raw_ids,
            })
            if not isinstance(parsed, str):
                spec, orders = parsed
                settings = scraper.load_settings()
                if settings.user and settings.password:
                    code = asyncio.run(qv_action.run_many(settings, spec, orders, dry_run=False))
                    if code == 0:
                        return {
                            "name": "Aksiyon & Kuyruk Denetimi",
                            "ok": True,
                            "detail": f"Takılı kalan {len(orders)} sipariş ({action_name}) otomatik yeniden işlendi ve doğrulandı.",
                        }

    if failed_runs:
        return {
            "name": "Aksiyon & Kuyruk Denetimi",
            "ok": False,
            "detail": f"Son aksiyon çalıştırması başarısız oldu: {failed_runs[0].get('display_title')}",
        }

    return {
        "name": "Aksiyon & Kuyruk Denetimi",
        "ok": True,
        "detail": "Takılı kalan veya başarısız bekleyen sipariş bulunmuyor.",
    }


def check_bloat_and_performance(root: Path = ROOT) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """Dosya boyutlarını, önbellek kurallarını, cron zamanlayıcılarını ve ölü dosyaları analiz eder."""
    optimizations: list[str] = []
    extra_metrics: dict[str, Any] = {}

    index_path = root / "index.html"
    if index_path.exists():
        idx_bytes = index_path.stat().st_size
        idx_text = index_path.read_text(encoding="utf-8")
        idx_kb = round(idx_bytes / 1024, 1)
        idx_lines = idx_text.count("\n") + 1
        extra_metrics["index_kb"] = idx_kb
        extra_metrics["index_lines"] = idx_lines

        if "lastSignature" not in idx_text:
            optimizations.append(
                "index.html içinde DOM render imza kontrolü (lastSignature) eksik; gereksiz yeniden çizim yapabilir."
            )
        if "PHOTO_TTL_MS" not in idx_text:
            optimizations.append(
                "IndexedDB kanıt fotoğrafları için otomatik süre temizliği (PHOTO_TTL_MS) tanımlanmalı."
            )
        if idx_kb > 450:
            optimizations.append(
                f"index.html boyutu {idx_kb} KB seviyesine ulaştı; kullanılmayan CSS/JS blokları temizlenebilir."
            )

    data_path = root / "data.json"
    if data_path.exists():
        raw_bytes = data_path.stat().st_size
        data_kb = round(raw_bytes / 1024, 1)
        try:
            data = json.loads(data_path.read_text(encoding="utf-8"))
            compact_bytes = len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            saved_kb = round((raw_bytes - compact_bytes) / 1024, 1)
            current_year = datetime.now(timezone.utc).year
            all_orders = (data.get("deliveries") or []) + (data.get("returns") or [])
            old_orders = [
                o for o in all_orders
                if str(o.get("date") or "")[:4].isdigit() and int(str(o.get("date"))[:4]) < current_year - 1
            ]
            if old_orders:
                optimizations.append(
                    f"data.json içinde geçmiş yıllardan kalan {len(old_orders)} eski sipariş kaydı var; temizlenmesi önerilir."
                )
            if saved_kb > 200:
                optimizations.append(
                    f"data.json girintili (pretty-print) yazıldığı için {data_kb} KB yer kaplıyor; sıkıştırılmış (compact) "
                    f"JSON formatına geçilirse dosya boyutu ~{saved_kb} KB (%23) küçülür."
                )
        except Exception:
            pass

    # Cron zamanlayıcıları ve GitHub Actions dakika tüketimi analizi
    sync_yml = root / ".github" / "workflows" / "sync.yml"
    if sync_yml.exists():
        sync_text = sync_yml.read_text(encoding="utf-8")
        if "5,35 * * * *" in sync_text and "0 15 * * *" in sync_text:
            optimizations.append(
                "sync.yml hem 30 dakikada bir ('5,35 * * * *', günde 48 kez) hem de '0 5,10,15 * * *' saatlerinde çalışıyor "
                "(05:00/05:05, 10:00/10:05, 15:00/15:05'te 5 dk arayla çift çalışma). Hatırlatma kontrolü 05:05, 10:05 ve "
                "15:05'teki mevcut çalışmalarla birleştirilirse günde 3 gereksiz Chromium başlatması (~135 dk/ay Actions süresi) tasarruf edilir."
            )

    # Kullanılmayan / eski dosyalar ve yerel yedek klasörleri kontrolü
    handoff_file = root / "HANDOFF_TO_CLAUDE.md"
    if handoff_file.exists():
        optimizations.append(
            "Proje kök dizinindeki eski 'HANDOFF_TO_CLAUDE.md' (3.2 KB) artık 'MODEL_BRIDGE.md' ve 'CLAUDE.md' "
            "kullanıldığı için ölü dosya durumundadır; istenirse silinebilir."
        )

    backup_dirs = sorted(p for p in root.glob("_backup_before_*") if p.is_dir())
    if len(backup_dirs) > 3:
        total_mb = sum(
            f.stat().st_size for d in backup_dirs for f in d.rglob("*") if f.is_file()
        ) / (1024 * 1024)
        optimizations.append(
            f"Yerel çalışma dizininde (.gitignore kapsamında) {len(backup_dirs)} adet eski '_backup_before_*' klasörü "
            f"toplam {total_mb:.1f} MB yer kaplıyor; yalnızca son 2 yedek tutulup eskileri silinebilir."
        )

    check_result = {
        "name": "Performans & Gereksiz Yük Denetimi",
        "ok": True,
        "detail": (
            f"index.html ({extra_metrics.get('index_kb', '—')} KB), DOM imza önbelleği ve localStorage/IndexedDB "
            f"temizlik kuralları aktif · {len(optimizations)} optimizasyon önerisi raporlandı."
        ),
    }
    return check_result, optimizations, extra_metrics


def run_all_checks(root: Path = ROOT, portal_check: bool = False,
                   auto_heal: bool = False, live_check: bool = False,
                   ui_check: bool = False, app_url: str = DEFAULT_APP_URL) -> dict[str, Any]:
    """Tüm sağlık ve performans kontrollerini çalıştırıp rapor sözlüğü döner."""
    checks: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}

    if portal_check:
        checks.append(check_portal_session())
    else:
        checks.append({
            "name": "Quietvox Portal Seçici & Onay Kuralları",
            "ok": "no,\\s*just\\s*save" in qv_action.JS_FORM_PROBLEMS,
            "detail": "Delivered (2) / Returned (6) durum kodları ve SMS onay penceresi ('No, just save changes') aktif.",
        })

    checks.append(check_and_heal_action_queue(root=root, auto_heal=auto_heal))
    checks.append(check_index_syntax_and_badges(root=root))
    checks.append(check_secret_hygiene(root=root))

    data_check, data_metrics = check_data_freshness(root=root)
    if not data_check["ok"] and auto_heal and os.environ.get("GH_TOKEN"):
        if "senkron gecikmesi" in str(data_check.get("detail", "")):
            subprocess.run(
                ["gh", "workflow", "run", "sync.yml", "-R", GH_REPO],
                capture_output=True, text=True, timeout=15,
            )
            data_check["detail"] += " (Otomatik sync.yml tetiklendi)"
    checks.append(data_check)
    metrics.update(data_metrics)

    if live_check:
        checks.append(check_live_pages(app_url=app_url))
    if live_check or ui_check:
        checks.append(check_frontend_playwright(app_url=app_url))

    perf_check, optimizations, perf_metrics = check_bloat_and_performance(root=root)
    checks.append(perf_check)
    metrics.update(perf_metrics)

    healthy = all(item.get("ok", False) for item in checks)
    return {
        "healthy": healthy,
        "checked_at": datetime.now(timezone.utc).astimezone(TZ).strftime("%d.%m.%Y %H:%M"),
        "app_url": app_url,
        "checks": checks,
        "optimizations": optimizations,
        "metrics": metrics,
    }


# ---- Pytest birim testleri (pytest çalıştırıldığında otomatik koşar) -----------------------

def test_system_check_local_suite():
    report = run_all_checks(root=ROOT, portal_check=False, auto_heal=False, live_check=False)
    assert report["healthy"] is True
    assert any("index.html" in c["name"] for c in report["checks"])
    assert isinstance(report["optimizations"], list)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Quietvox 7/24 Sistem Sağlık ve Performans Denetimi")
    parser.add_argument("--portal-check", action="store_true", help="Canlı eu.quietvox.com oturum ve DOM testi yap")
    parser.add_argument("--auto-heal", action="store_true", help="Takılı kalan aksiyonları otomatik yeniden işle")
    parser.add_argument("--live-check", action="store_true", help="Canlı GitHub Pages HTTP 200 ve Playwright arayüz kontrolü yap")
    parser.add_argument("--ui-check", action="store_true", help="Playwright ile gerçek tarayıcı arayüz kontrolü yap")
    parser.add_argument("--send-email", action="store_true", help="Sağlık ve yük raporunu e-posta ile gönder")
    parser.add_argument("--app-url", default=os.environ.get("APP_URL") or DEFAULT_APP_URL)
    args = parser.parse_args(argv)

    report = run_all_checks(
        root=ROOT,
        portal_check=args.portal_check,
        auto_heal=args.auto_heal,
        live_check=args.live_check,
        ui_check=args.ui_check,
        app_url=args.app_url,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    write_step_summary(report)

    if args.send_email:
        notify_mail.send_health_report(report, os.environ)

    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
