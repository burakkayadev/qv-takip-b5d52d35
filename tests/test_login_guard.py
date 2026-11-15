"""Giriş hatası koruması: yedek şifre, siparişleri koruma, saatte en fazla bir alarm e-postası."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import notify_mail
import scraper

NOW = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)
ORDERS = [{"id": "QV-1", "date": "2026-11-26", "waybill": {}, "dr_form": {}, "raw": {}}]


def previous_doc(**extra):
    return {"schema_version": 1, "demo": False, "status": "ok", "status_message": "",
            "generated_at": "2026-11-25T08:00:00Z", "deliveries": list(ORDERS), "returns": [], **extra}


def stub_senders(monkeypatch):
    sent = {"mail": 0, "ntfy": 0}

    def fake_mail(settings, now):
        sent["mail"] += 1
        return True

    def fake_ntfy(*args, **kwargs):
        sent["ntfy"] += 1
        return True

    monkeypatch.setattr(scraper, "send_login_alert", fake_mail)
    monkeypatch.setattr(scraper, "send_ntfy", fake_ntfy)
    return sent


def read(settings):
    return json.loads(settings.output.read_text(encoding="utf-8"))


def test_backup_settings_only_when_backup_differs(settings):
    assert scraper.backup_settings(settings) is None
    same = scraper.load_settings({"QV_USER": "u", "QV_PASS": "abcd1", "QV_PASS_BACKUP": "abcd1"})
    assert scraper.backup_settings(same) is None
    other = scraper.load_settings({"QV_USER": "u", "QV_PASS": "eski-1", "QV_PASS_BACKUP": "yeni-2"})
    backup = scraper.backup_settings(other)
    assert (backup.password, backup.http_pass) == ("yeni-2", "yeni-2")  # HTTP Basic ana şifreyi izliyordu
    separate = scraper.load_settings({"QV_USER": "u", "QV_PASS": "eski-1", "QV_PASS_BACKUP": "yeni-2",
                                      "QV_HTTP_PASS": "kapi-3"})
    assert scraper.backup_settings(separate).http_pass == "kapi-3"


def test_login_alert_due_respects_one_hour():
    assert scraper.login_alert_due(None, NOW)
    assert scraper.login_alert_due({"login_alert_at": "bozuk"}, NOW)
    assert not scraper.login_alert_due({"login_alert_at": "2026-11-25T08:05:00Z"}, NOW)
    assert scraper.login_alert_due({"login_alert_at": "2026-11-25T08:00:00Z"}, NOW)


def test_login_failure_keeps_orders_and_mails_at_most_hourly(settings, monkeypatch):
    sent = stub_senders(monkeypatch)
    settings.output.write_text(json.dumps(previous_doc()), encoding="utf-8")

    assert scraper.handle_login_failure(settings, previous_doc(), NOW) == scraper.EXIT_LOGIN
    first = read(settings)
    assert first["deliveries"] == ORDERS and first["status"] == "login_failed"
    assert first["login_alert_at"] == "2026-11-25T09:00:00Z"
    assert sent == {"mail": 1, "ntfy": 1}

    scraper.handle_login_failure(settings, first, NOW + timedelta(minutes=30))
    second = read(settings)
    assert sent == {"mail": 1, "ntfy": 1}  # 30 dk sonra: e-posta da ntfy de tekrarlanmaz
    assert second["login_alert_at"] == first["login_alert_at"]

    scraper.handle_login_failure(settings, second, NOW + timedelta(minutes=61))
    third = read(settings)
    assert sent["mail"] == 2 and third["deliveries"] == ORDERS
    assert third["login_alert_at"] == "2026-11-25T10:01:00Z"  # yeni zaman kaydedildi: sonraki 1 saat sessiz

    scraper.handle_login_failure(settings, third, NOW + timedelta(minutes=91))
    assert sent["mail"] == 2


def test_unsent_alert_is_retried_next_run(settings, monkeypatch):
    stub_senders(monkeypatch)
    monkeypatch.setattr(scraper, "send_login_alert", lambda s, n: False)  # SMTP tanımlı değil / hata
    scraper.handle_login_failure(settings, previous_doc(), NOW)
    assert "login_alert_at" not in read(settings)


def test_alert_mail_subject_steps_and_recipients(monkeypatch):
    captured = {}
    monkeypatch.setattr(notify_mail, "send", lambda message, host, port, user, password: captured.update(
        message=message, host=host, port=port))
    env = {"MAIL_TO": "a@example.com, b@example.org", "SMTP_USER": "gonderen@example.com", "SMTP_PASS": "x"}

    assert notify_mail.send_login_alert(NOW, env, "https://ornek.github.io/repo/")
    message = captured["message"]
    assert message["Subject"] == "Quietvox Oturum Uyarisi - Sifre Kontrolu Gerekiyor"
    assert message["From"] == "Quietvox Takip Sistemi <gonderen@example.com>"
    assert message["Reply-To"] == "gonderen@example.com"
    assert message["To"] == "a@example.com, b@example.org"
    text = message.get_body(("plain",)).get_content()
    assert "25.11.2026 12:00" in text and "Kaydet ve Dene" in text and "QV_PASS" in text
    assert "hiçbir sipariş silinmedi" in text
    assert (captured["host"], captured["port"]) == ("smtp.gmail.com", 465)


def test_alert_to_overrides_mail_to_and_missing_smtp_sends_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(notify_mail, "send", lambda *a: calls.append(a))
    assert not notify_mail.send_login_alert(NOW, {"MAIL_TO": "a@example.com"})
    assert notify_mail.send_login_alert(NOW, {"MAIL_TO": "a@example.com", "ALERT_TO": "c@example.net",
                                             "SMTP_USER": "g@example.com", "SMTP_PASS": "x"})
    assert calls[0][0]["To"] == "c@example.net"
