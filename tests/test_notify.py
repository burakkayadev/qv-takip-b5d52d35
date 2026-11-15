"""notify_mail.py için testler (kurgusal veri; e-posta gönderilmez)."""
from datetime import datetime, timezone

import notify_mail

DATA = {
    "deliveries": [{"id": "2000000001", "date": "2026-11-16", "location_name": "Demo Otel",
                    "waybill": {"client": "Örnek Travel", "tour_end": "2026-11-20"},
                    "dr_form": {"qv_booking_id": "2000000001", "clients": "20",
                                "equipment": [{"item": "QV9 RX EU(QV9.1)", "quantity": "20"},
                                              {"item": "QV9 TX EU(QV9.2)", "quantity": "1"},
                                              {"item": "Lanyards", "quantity": "VIKING Lanyards"}]}}],
    "returns": [{"id": "2000000001", "date": "2026-11-21", "dr_form": {"qv_booking_id": "2000000001"}}],
}
PRINTED = datetime(2026, 11, 15, 9, 30, tzinfo=timezone.utc)


def test_parse_orders_validates_and_dedupes():
    text = "2000000001:2026-11-16, kötü, 2000000001:2026-11-17,123:2026-11-16,2000000002:2026-13-01x"
    assert notify_mail.parse_orders(text) == [("2000000001", "2026-11-16")]


def test_rows_contain_hotel_agency_pax_equipment_dates_and_time():
    rows = notify_mail.build_rows([("2000000001", "2026-11-16"), ("2000000099", "2026-11-17")], DATA, PRINTED)

    assert rows[0] == ["2000000001", "Demo Otel", "Örnek Travel", "20", "RX 20, TX 1 (3 kalem)",
                       "16.11.2026", "21.11.2026", "15.11.2026 12:30"]
    assert rows[1][0] == "2000000099" and rows[1][1] == "—" and rows[1][5] == "17.11.2026"


def test_message_subject_and_bodies():
    rows = notify_mail.build_rows([("2000000001", "2026-11-16")], DATA, PRINTED)
    message = notify_mail.build_message(rows, "gonderen@example.com", ["a@example.com", "b@example.com"])

    assert message["Subject"] == "Quietvox: 1 Sipariş Formu Basıldı ve Teslim Edildi (Delivered)"
    assert message["To"] == "a@example.com, b@example.com"
    html = message.get_body(("html",)).get_content()
    assert "<table" in html and "Demo Otel" in html and "Delivered" in html
    assert "Demo Otel" in message.get_body(("plain",)).get_content()


def test_html_escapes_values():
    html = notify_mail.build_html([["1", "<b>x</b>", "", "", "", "", "", ""]])
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html


def test_main_without_smtp_secrets_does_not_send(monkeypatch, capsys):
    monkeypatch.setenv("ORDERS", "2000000001:2026-11-16")
    for name in ("MAIL_TO", "SMTP_USER", "SMTP_PASS"):
        monkeypatch.delenv(name, raising=False)

    def fail(*args):
        raise AssertionError("gönderilmemeli")

    monkeypatch.setattr(notify_mail, "send", fail)

    assert notify_mail.main() == 0
    assert "gönderilmedi" in capsys.readouterr().out


def test_main_sends_to_all_recipients(monkeypatch):
    sent = {}
    monkeypatch.setenv("ORDERS", "2000000001:2026-11-16")
    monkeypatch.setenv("MAIL_TO", "a@example.com, b@example.com")
    monkeypatch.setenv("SMTP_USER", "gonderen@example.com")
    monkeypatch.setenv("SMTP_PASS", "uygulama-sifresi")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_PORT", raising=False)
    monkeypatch.setattr(notify_mail, "send", lambda msg, host, port, user, pw: sent.update(msg=msg, host=host, port=port))

    assert notify_mail.main() == 0
    assert sent["host"] == "smtp.gmail.com" and sent["port"] == 465
    assert sent["msg"]["To"] == "a@example.com, b@example.com"


def test_send_update_notification_subject_and_body(monkeypatch, tmp_path):
    vfile = tmp_path / "version.json"
    vfile.write_text('{"version": "4.0.0", "build": 77, "commit": "abc1234", "updated_at": "2026-11-15T18:30:00Z"}',
                     encoding="utf-8")
    sent = {}
    env = {
        "MAIL_TO": "a@example.com, b@example.com",
        "SMTP_USER": "gonderen@example.com",
        "SMTP_PASS": "uygulama-sifresi",
        "COMMIT_MESSAGE": "fix: toplu Hazırlandı senkronizasyonu ve otomatik e-posta",
        "APP_URL": "https://burakkayadev.github.io/qv-takip-b5d52d35/",
    }
    monkeypatch.setattr(notify_mail, "send", lambda msg, host, port, user, pw: sent.update(msg=msg, host=host, port=port))

    assert notify_mail.send_update_notification(env, version_path=vfile) is True
    msg = sent["msg"]
    assert msg["Subject"] == "🚀 Quietvox Takip Uygulaması Güncellendi (Yeni Sürüm Hazır)"
    assert msg["To"] == "a@example.com, b@example.com"
    plain = msg.get_body(("plain",)).get_content()
    html = msg.get_body(("html",)).get_content()
    assert "Build #77" in plain and "fix: toplu Hazırlandı" in plain
    assert "https://burakkayadev.github.io/qv-takip-b5d52d35/" in plain
    assert "Build #77" in html and "https://burakkayadev.github.io/qv-takip-b5d52d35/" in html

