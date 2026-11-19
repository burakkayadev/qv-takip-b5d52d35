#!/usr/bin/env python3
"""Quietvox sistem denetimi — 5 SERT KAPI. Biri bile kırmızıysa çıkış kodu 1 (+ e-posta uyarısı).

  Kapı 1  Veri tazeliği: yayındaki data.json generated_at ≤ 90 dk ve durum "ok".
  Kapı 2  İş akışları: son 24 saatte başarısız sync.yml / action.yml çalıştırması, kendisinden
          sonraki başarılı bir çalıştırmayla (işlemde: aynı siparişler) telafi edilmemişse kırmızı.
  Kapı 3  Portal ↔ data.json: canlı portal SALT OKUNUR okunur (portal_audit.py); en az 15 kayıt
          (hedef 3 sipariş data.json penceresindeyken dahil) karşılaştırılır; tek uyuşmazlık kırmızı.
  Kapı 4  Canlı uygulama: ?t= ile taze veri, gerçek duruma bağlı rozet, doğru sayaçlar (ui_gates.py).
  Kapı 5  Sıfır gerileme: hata yok, filtreler, Otele Göre Grupla, radar, tek sayfa PDF, sürüm (ui_gates.py).

Kullanım:
  python tests/system_check.py --send-email                                   # CI: 5 kapı, canlı adres
  python tests/system_check.py --gates 4,5 --app-url http://localhost:8080/   # push öncesi yerel
  python tests/system_check.py --gates 4,5 --app-url http://localhost:8080/ --simulate-fresh

Bu denetim portala ASLA yazmaz. Eski sürümdeki --auto-heal onaysız portal yazımı yaptığı için,
"QV_USER yoksa geçti" ve "son çalıştırma başarılıysa eski hataları yok say" kuralları da sağlık
durumunu olduğundan iyi gösterdiği için kaldırıldı.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
for _path in (ROOT, Path(__file__).resolve().parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import notify_mail  # noqa: E402
import portal_audit  # noqa: E402
import scraper  # noqa: E402
import ui_gates  # noqa: E402

log = logging.getLogger("system_check")

TZ = ZoneInfo("Europe/Istanbul")
DEFAULT_APP_URL = "https://burakkayadev.github.io/qv-takip-b5d52d35/"
GH_REPO = os.environ.get("GITHUB_REPOSITORY") or "burakkayadev/qv-takip-b5d52d35"
MAX_DATA_AGE_MINUTES = 90
RUN_WINDOW = timedelta(hours=24)
MIN_COMPARED = 15
FAILED_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})
DEPLOY_WAIT_SECONDS = 600
ALL_GATES = (1, 2, 3, 4, 5)
LOCAL_HOSTS = ("http://localhost", "http://127.0.0.1")
_ORDER_ID_RE = re.compile(r"\b\d{6,15}\b")
_VERSION_LINE_RE = re.compile(r"var CURRENT_BUILD = \d+;|const VERSION = 'b\d+';")


def gate(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail}


# --- Kapı 1: veri tazeliği -------------------------------------------------------------------------

def fetch_live_document(app_url: str) -> dict[str, Any]:
    return json.loads(ui_gates.http_text(f"{app_url}data.json?t={int(time.time() * 1000)}"))


def gate_freshness(document: dict[str, Any] | None, error: str = "",
                   now: datetime | None = None) -> dict[str, Any]:
    name = "Kapı 1 · Veri tazeliği (≤90 dk)"
    if document is None:
        return gate(name, False, f"yayındaki data.json okunamadı: {error}")
    age = ui_gates.data_age_minutes(document, now)
    stamp = document.get("generated_at")
    if age is None:
        return gate(name, False, f"generated_at geçersiz: {stamp!r}")
    problems = []
    if age > MAX_DATA_AGE_MINUTES:
        problems.append(f"veri {round(age)} dk eski (son çekim {stamp})")
    status = str(document.get("status") or "")
    if status != "ok":
        problems.append(f"veri durumu '{status}' {document.get('status_message') or ''}".strip())
    return gate(name, not problems, "; ".join(problems) or f"veri {round(age)} dk önce çekildi ({stamp}), durum ok")


# --- Kapı 2: iş akışı çalıştırmaları ---------------------------------------------------------------

def gh_runs(workflow: str, limit: int) -> list[dict[str, Any]]:
    fields = "databaseId,conclusion,status,createdAt,displayTitle,url,headBranch"
    result = subprocess.run(["gh", "run", "list", "-R", GH_REPO, "-w", workflow, "-L", str(limit), "--json", fields],
                            capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"gh run list {workflow}: {result.stderr.strip()[:200]}")
    return json.loads(result.stdout or "[]")


def _created(run: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(run.get("createdAt")).replace("Z", "+00:00"))


def _action_key(run: dict[str, Any]) -> tuple[str, frozenset[str]]:
    """İşlem çalıştırmasının türü (delivered/returned) ve başlığındaki sipariş kimlikleri."""
    title = str(run.get("displayTitle") or "")
    return title.split("·", 1)[0].strip(), frozenset(_ORDER_ID_RE.findall(title))


def sync_covered(run: dict[str, Any], later: list[dict[str, Any]]) -> bool:
    """Senkron hatası, sonraki herhangi bir başarılı senkronla telafi edilir."""
    return bool(later)


def action_covered(run: dict[str, Any], later: list[dict[str, Any]]) -> bool:
    """İşlem hatası yalnızca AYNI siparişler sonradan aynı işlemle başarılı olduysa telafi edilmiştir."""
    kind, ids = _action_key(run)
    if not ids:
        return any(_action_key(other)[0] == kind for other in later)
    covered: set[str] = set()
    for other in later:
        other_kind, other_ids = _action_key(other)
        if other_kind == kind:
            covered |= other_ids
    return ids <= covered


def uncompensated(runs: list[dict[str, Any]], now: datetime,
                  covers: Callable[[dict[str, Any], list[dict[str, Any]]], bool]) -> list[dict[str, Any]]:
    """Son 24 saatteki başarısız çalıştırmalardan sonrasında telafi EDİLMEYENLER."""
    done = sorted((r for r in runs if r.get("status") == "completed"), key=_created)
    since = now - RUN_WINDOW
    bad = []
    for index, run in enumerate(done):
        if run.get("conclusion") not in FAILED_CONCLUSIONS or _created(run) < since:
            continue
        later = [r for r in done[index + 1:] if r.get("conclusion") == "success"]
        if not covers(run, later):
            bad.append(run)
    return bad


def gate_workflow_runs(now: datetime | None = None,
                       fetch: Callable[[str, int], list[dict[str, Any]]] = gh_runs) -> dict[str, Any]:
    name = "Kapı 2 · İş akışları (24 sa, telafisiz hata yok)"
    now = now or datetime.now(timezone.utc)
    try:
        sync_runs, action_runs = fetch("sync.yml", 200), fetch("action.yml", 100)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return gate(name, False, f"çalıştırma listesi okunamadı: {exc}")
    bad = [("sync.yml", r) for r in uncompensated(sync_runs, now, sync_covered)]
    bad += [("action.yml", r) for r in uncompensated(action_runs, now, action_covered)]
    if bad:
        rows = "; ".join(f"{wf} '{r.get('displayTitle')}' {r.get('createdAt')} {r.get('conclusion')} {r.get('url')}"
                         for wf, r in bad[:6])
        return gate(name, False, f"telafi edilmemiş {len(bad)} başarısız çalıştırma: {rows}")
    recent = [r for r in sync_runs + action_runs if r.get("status") == "completed" and _created(r) >= now - RUN_WINDOW]
    success = sum(1 for r in recent if r.get("conclusion") == "success")
    return gate(name, True, f"son 24 saatte {len(recent)} tamamlanan çalıştırma ({success} başarılı); telafisiz hata yok")


# --- Kapı 3: canlı portal ↔ data.json --------------------------------------------------------------

def targets_in(document: dict[str, Any]) -> list[str]:
    """Hedef 3 siparişten data.json penceresinde olanlar (pencereden çıkınca zorunlu değildir)."""
    ids = {portal_audit._REPEAT_SUFFIX.sub("", str(order.get("id") or ""))
           for key in portal_audit.LIST_KEYS for order in document.get(key) or [] if isinstance(order, dict)}
    return [oid for oid in portal_audit.DEFAULT_TARGETS if oid in ids]


def gate_portal(document: dict[str, Any] | None) -> dict[str, Any]:
    name = "Kapı 3 · Canlı portal ↔ data.json"
    if document is None:
        return gate(name, False, "karşılaştırılacak data.json okunamadı")
    settings = scraper.load_settings()
    if not (settings.user and settings.password):
        return gate(name, False, "QV_USER / QV_PASS tanımlı değil: portal karşılaştırması YAPILAMADI")
    try:
        report = asyncio.run(portal_audit.run_audit(settings, document, targets_in(document), 62, 10))
    except Exception as exc:
        return gate(name, False, f"portal okunamadı: {type(exc).__name__}: {str(exc)[:200]}")
    portal_audit.write_report(report, ROOT / "debug" / "portal_audit.json")
    issues = portal_audit.problems(report)
    compared = sum(info["events"] for info in report.get("calendars", {}).values())
    if compared < MIN_COMPARED:
        issues.append(f"yalnızca {compared} portal kaydı karşılaştırılabildi (en az {MIN_COMPARED})")
    if issues:
        return gate(name, False, f"{len(issues)} sorun: " + " | ".join(issues[:10]))
    targets = ", ".join(f"{oid}={(info.get('form') or {}).get('label') or '?'}"
                        for oid, info in report.get("targets", {}).items()) or "pencere dışında"
    return gate(name, True, f"{compared} portal kaydı data.json ile birebir; hedefler: {targets}")


# --- Kapı 4-5 ve yayın beklemesi -------------------------------------------------------------------

def _normalized(html: str) -> str:
    return _VERSION_LINE_RE.sub("", html.replace("\r\n", "\n"))


def wait_for_deploy(app_url: str, timeout: int = DEPLOY_WAIT_SECONDS) -> str:
    """Push sonrası: yayındaki index.html depodakiyle (sürüm satırı hariç) aynı olana dek bekler."""
    local = _normalized((ROOT / "index.html").read_text(encoding="utf-8"))
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if _normalized(ui_gates.http_text(f"{app_url}index.html?t={int(time.time() * 1000)}")) == local:
                return ""
        except OSError as exc:
            log.warning("Yayın okunamadı, yeniden denenecek (%s).", exc)
        time.sleep(20)
    return f"yayın {timeout // 60} dakikada güncellenmedi (canlı index.html depodakiyle aynı değil)"


def run_gates(gates: tuple[int, ...], app_url: str, pin: str, simulate_fresh: bool,
              wait_deploy: bool) -> dict[str, Any]:
    """Seçilen kapıları sırayla çalıştırır ve rapor sözlüğünü döner."""
    checks: list[dict[str, Any]] = []
    document, error = None, ""
    if {1, 3} & set(gates):
        try:
            document = fetch_live_document(app_url)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    if 1 in gates:
        checks.append(gate_freshness(document, error))
    if 2 in gates:
        checks.append(gate_workflow_runs())
    if 3 in gates:
        checks.append(gate_portal(document))
    if {4, 5} & set(gates):
        deploy_problem = wait_for_deploy(app_url) if wait_deploy else ""
        gate4, gate5 = asyncio.run(ui_gates.run_ui_gates(app_url, pin, simulate_fresh))
        if deploy_problem:
            gate5 = {**gate5, "ok": False, "detail": f"{deploy_problem} | {gate5['detail']}"}
        checks += [check for check, number in ((gate4, 4), (gate5, 5)) if number in gates]
    return {
        "healthy": bool(checks) and all(check["ok"] for check in checks),
        "checked_at": datetime.now(TZ).strftime("%d.%m.%Y %H:%M"),
        "app_url": app_url, "checks": checks, "optimizations": [], "metrics": {},
    }


def write_step_summary(report: dict[str, Any]) -> None:
    """GitHub Actions çalışma özetine Türkçe tablo yazar."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"## Quietvox 5 Kapı Denetimi ({report['checked_at']} İstanbul)", "",
             f"**Sonuç:** {'✅ TÜM KAPILAR YEŞİL' if report['healthy'] else '❌ KIRMIZI KAPI VAR'}", "",
             "| Kapı | Durum | Ayrıntı |", "|---|---|---|"]
    for check in report["checks"]:
        detail = str(check["detail"]).replace("|", "\\|")
        lines.append(f"| {check['name']} | {'✅' if check['ok'] else '❌'} | {detail} |")
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as exc:
        log.warning("Çalışma özeti yazılamadı: %s", exc)


def parse_gates(text: str) -> tuple[int, ...]:
    gates = tuple(sorted({int(part) for part in text.split(",") if part.strip()}))
    if not gates or any(number not in ALL_GATES for number in gates):
        raise argparse.ArgumentTypeError(f"kapılar 1-5 arasında olmalı: {text}")
    return gates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Quietvox 5 sert kapı sistem denetimi")
    parser.add_argument("--gates", type=parse_gates, default=ALL_GATES, help="ör. 1,2,3,4,5 ya da 4,5")
    parser.add_argument("--app-url", default=os.environ.get("APP_URL") or DEFAULT_APP_URL)
    parser.add_argument("--simulate-fresh", action="store_true", help="YALNIZCA yerel adreste: veriyi taze göster")
    parser.add_argument("--wait-deploy", action="store_true", help="push sonrası yayının güncellenmesini bekle")
    parser.add_argument("--send-email", action="store_true", help="kırmızı kapı varsa uyarı e-postası gönder")
    parser.add_argument("--out", default=str(ROOT / "debug" / "system_check.json"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    app_url = args.app_url if args.app_url.endswith("/") else args.app_url + "/"
    if args.simulate_fresh and not app_url.startswith(LOCAL_HOSTS):
        parser.error("--simulate-fresh yalnızca yerel adreste (localhost) kullanılabilir")
    report = run_gates(args.gates, app_url, os.environ.get("QV_APP_PIN") or "", args.simulate_fresh, args.wait_deploy)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for check in report["checks"]:
        log.info("%s %s — %s", "YEŞİL  " if check["ok"] else "KIRMIZI", check["name"], check["detail"])
    write_step_summary(report)
    if not report["healthy"] and args.send_email:
        notify_mail.send_health_report(report, os.environ)
    log.info("SONUÇ: %s", "TÜM KAPILAR YEŞİL" if report["healthy"] else "KIRMIZI KAPI VAR — push yapılmamalı")
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
