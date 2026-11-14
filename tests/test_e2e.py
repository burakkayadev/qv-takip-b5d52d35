"""Uçtan uca test: scraper.py gerçek Chromium ile yerel sahte siteye karşı çalıştırılır.

Kökteki gerçek data.json'a ASLA yazılmaz; çıktı QV_OUTPUT ile geçici dizine yönlenir.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

pytest.importorskip("playwright")

from mock_site import server as mock  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 11, 25)  # pencere 2026-12-04'e kadar: ay geçişi sınanır
TOPIC = "kurgusal-test-konusu"


@pytest.fixture(scope="module")
def site():
    instance = mock.MockSite(TODAY).start()
    yield instance
    instance.stop()


@pytest.fixture(autouse=True)
def clean_site(site):
    site.reset()


def run_scraper(site, tmp_path: Path, **overrides: str) -> subprocess.CompletedProcess:
    """scraper.py'yi ayrı süreçte, yalnızca test ortam değişkenleriyle çalıştırır."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("QV_", "NTFY_")) and k != "SEND_REMINDER"}
    env.update({
        "QV_BASE_URL": site.base_url, "QV_USER": mock.FORM_USER, "QV_PASS": mock.FORM_PASS,
        "QV_HTTP_USER": mock.BASIC_USER, "QV_HTTP_PASS": mock.BASIC_PASS,
        "QV_OUTPUT": str(tmp_path / "data.json"), "QV_COOKIES": str(tmp_path / "cookies.json"),
        "QV_DEBUG_DIR": str(tmp_path / "debug"), "QV_TODAY": TODAY.isoformat(),
        "NTFY_URL": site.base_url + "/ntfy", "QV_MODAL_TIMEOUT_MS": "5000",
        "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
    })
    env.update(overrides)
    for name in [name for name, value in env.items() if value is None]:
        del env[name]
    return subprocess.run([sys.executable, str(ROOT / "scraper.py")], env=env, cwd=str(tmp_path),
                          capture_output=True, text=True, encoding="utf-8", timeout=300)


def read_output(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "data.json").read_text(encoding="utf-8"))


def iso(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


def expected_waybill(booking: str) -> dict:
    order = mock.ORDERS[booking]
    return {
        "tour_manager_name": order["manager"], "tour_manager_phone": order["manager_phone"],
        "tour_reference": order["reference"], "qv_id": booking, "client": order["client"],
        "delivery_location": "\n".join(order["delivery"]), "delivery_phone": order["delivery_phone"],
        "return_location": "\n".join(order["returning"]),
        "tour_start": iso(order["start"]), "tour_end": iso(order["end"]),
    }


def expected_dr_form(booking: str) -> dict:
    order = mock.ORDERS[booking]
    return {
        "customer": order["client"], "reference": order["reference"], "qv_booking_id": booking,
        "clients": order["clients"], "groups": order["groups"], "instruction_language": order["language"],
        "service_start_date": iso(order["start"]), "delivery_station": order["station"], "qr_content": booking,
        "equipment": [{"item": i, "quantity": q, "weight": w} for i, q, w in order["equipment"]],
        "total_weight": order["total"],
    }


def seed_previous(tmp_path: Path, **changes) -> dict:
    """Önceki çalışmadan kalmış gibi bir data.json bırakır."""
    def old(order_id: str, calendar: str, kind: str = "delivery") -> dict:
        return {"id": order_id, "type": kind, "date": iso(2), "time": "", "cruise": "Cruises" in calendar,
                "source_calendar": calendar, "title": "Eski kayıt", "location_name": "Eski Kurgu Otel",
                "waybill": {}, "dr_form": {}, "raw": {}}

    document = {
        "schema_version": 1, "demo": False, "status": "ok", "status_message": "",
        "generated_at": "2026-11-20T08:00:00Z", "timezone": "Europe/Istanbul", "window_days": 10,
        "deliveries": [old("ESKI-GEMI", "Cruises Calendar Delivery"), old("ESKI-NORMAL", "Calendar Delivery")],
        "returns": [old("ESKI-IADE", "Calendar Return", "return")], **changes,
    }
    (tmp_path / "data.json").write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return document


def test_full_sync_produces_schema_compliant_data(site, tmp_path):
    result = run_scraper(site, tmp_path, NTFY_TOPIC=TOPIC, SEND_REMINDER="1", QV_DEBUG="1")
    assert result.returncode == 0, result.stdout + result.stderr
    document = read_output(tmp_path)

    assert list(document) == ["schema_version", "demo", "status", "status_message", "generated_at",
                              "timezone", "window_days", "deliveries", "returns"]
    assert (document["demo"], document["status"], document["status_message"]) == (False, "ok", "")
    assert (document["timezone"], document["window_days"]) == ("Europe/Istanbul", 10)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", document["generated_at"])

    # Pencere + tekilleştirme + sıralama (tarih, saat, id)
    deliveries = [(o["id"], o["date"], o["time"], o["cruise"], o["source_calendar"]) for o in document["deliveries"]]
    assert deliveries == [
        ("QV-700101", iso(0), "09:30", False, "Calendar Delivery"),
        ("QV-700102", iso(1), "", False, "Calendar Delivery"),
        ("QV-700201", iso(1), "14:00", True, "Cruises Calendar Delivery"),
        ("QV-700103", iso(8), "14:00", False, "Calendar Delivery"),  # sonraki ay görünümü de taranır
        ("QV-700202", iso(9), "", True, "Cruises Calendar Delivery"),  # pencerenin son günü
    ]
    returns = [(o["id"], o["date"], o["time"], o["cruise"], o["source_calendar"]) for o in document["returns"]]
    assert returns == [
        ("QV-700090", iso(1), "11:00", False, "Calendar Return"),
        ("QV-700301", iso(2), "16:30", True, "Cruises Calendar Return"),  # menüde yok: tahmini URL
        ("QV-700101", iso(6), "", False, "Calendar Return"),  # teslimatta da var: meşru
    ]

    # Dört farklı modal/belge biçiminin hepsi aynı şemaya ayrışmalı
    for order in document["deliveries"] + document["returns"]:
        assert order["waybill"] == expected_waybill(order["id"]), order["id"]
        assert order["dr_form"] == expected_dr_form(order["id"]), order["id"]
        assert order["type"] == ("delivery" if order in document["deliveries"] else "return")
        assert isinstance(order["raw"], dict)

    by_id = {o["id"]: o for o in document["deliveries"]}
    assert by_id["QV-700101"]["title"] == "Aurora Reisen - Hotel Lale Sarayı"
    assert by_id["QV-700101"]["location_name"] == "Hotel Lale Sarayı"
    assert by_id["QV-700101"]["raw"] == {"Internal Note": "kurgusal not"}
    assert by_id["QV-700201"]["title"] == "MS Poyraz - Galata Demo Terminal"
    assert [o["location_name"] for o in document["returns"]] == [
        "Haliç Demo Residence", "Karaköy Demo Rıhtımı", "Hotel Lale Sarayı"]

    # Hatırlatma: yarının (bugün+1) özetleri UTF-8 gövdeyle gitmeli
    assert site.notifications == [{
        "topic": TOPIC, "title": "Quietvox",
        "body": "📦 Yarın 2 Teslimat (Pera Köşk Otel, Galata Demo Terminal) ve 🔄 1 İade Alımı (Haliç Demo Residence) var.",
    }]

    # Çift giriş (form + doğrulama paneli) yapıldı, çerezler kaydedildi, sırlar loglanmadı
    assert site.login_posts == 2
    assert json.loads((tmp_path / "cookies.json").read_text(encoding="utf-8"))
    assert mock.FORM_PASS not in result.stdout + result.stderr
    assert mock.BASIC_PASS not in result.stdout + result.stderr
    assert (tmp_path / "debug" / "delivery_view0.html").exists()
    assert (tmp_path / "debug" / "delivery_modal1.html").exists()

    # İkinci çalışma: çerezle giriş atlanır, içerik aynı ve taze olduğundan dosyaya dokunulmaz
    before = (tmp_path / "data.json").read_bytes()
    site.login_posts = 0
    site.notifications.clear()
    second = run_scraper(site, tmp_path)
    assert second.returncode == 0, second.stdout + second.stderr
    assert site.login_posts == 0
    assert "GET /login.html" not in site.requests[-40:]
    assert (tmp_path / "data.json").read_bytes() == before
    assert site.notifications == []  # SEND_REMINDER yok


def test_broken_calendar_yields_partial_status_and_keeps_old_records(site, tmp_path):
    seed_previous(tmp_path)
    site.broken_calendars.add("cruise_delivery")
    result = run_scraper(site, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    document = read_output(tmp_path)
    assert document["status"] == "partial"
    assert "Cruises Calendar Delivery" in document["status_message"]
    ids = [o["id"] for o in document["deliveries"]]
    assert "ESKI-GEMI" in ids  # okunamayan takvimin eski kaydı korunur
    assert "ESKI-NORMAL" not in ids and "QV-700201" not in ids
    assert {"QV-700101", "QV-700102", "QV-700103"} <= set(ids)
    assert [o["id"] for o in document["returns"]] == ["QV-700090", "QV-700301", "QV-700101"]
    assert document["generated_at"] != "2026-11-20T08:00:00Z"


def test_login_failure_keeps_orders_notifies_once_and_exits_nonzero(site, tmp_path):
    previous = seed_previous(tmp_path)
    site.reject_form_login = True
    result = run_scraper(site, tmp_path, NTFY_TOPIC=TOPIC, SEND_REMINDER="1")
    assert result.returncode != 0
    document = read_output(tmp_path)
    assert document["status"] == "login_failed" and document["status_message"]
    assert document["deliveries"] == previous["deliveries"] and document["returns"] == previous["returns"]
    assert document["generated_at"] == previous["generated_at"]
    assert [n["body"] for n in site.notifications] == ["⚠️ Quietvox oturumu açılamadı, şifreyi kontrol edin"]
    assert site.login_posts == 3  # en fazla 3 form turu
    assert not (tmp_path / "cookies.json").exists()
    assert mock.FORM_PASS not in result.stdout + result.stderr

    again = run_scraper(site, tmp_path, NTFY_TOPIC=TOPIC)
    assert again.returncode != 0
    assert len(site.notifications) == 1  # zaten login_failed: bildirim tekrarlanmaz


def test_rejected_basic_auth_is_reported_as_login_failure(site, tmp_path):
    result = run_scraper(site, tmp_path, QV_HTTP_PASS="yanlis-kapi-sifresi")
    assert result.returncode != 0
    document = read_output(tmp_path)
    assert (document["status"], document["demo"]) == ("login_failed", False)
    assert document["deliveries"] == [] and document["returns"] == []
    assert site.login_posts == 0


def test_missing_credentials_exit_with_clear_error(site, tmp_path):
    result = run_scraper(site, tmp_path, QV_PASS="")
    assert result.returncode == 2
    assert "QV_USER ve QV_PASS" in result.stdout
    assert not (tmp_path / "data.json").exists()
