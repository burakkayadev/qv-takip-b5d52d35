#!/usr/bin/env python3
"""Hotmail/Outlook Spam Korumalı Kurumsal E-posta Motoru.

Kullanıldığı yerler:
  1. Form basıldığında bilgilendirme (".github/workflows/notify.yml")
  2. Yeni sürüm yayına alındığında güncelleme bildirimi (".github/workflows/version.yml" -> --update)
  3. Quietvox oturum/şifre hatası acil uyarısı ("scraper.py" -> send_login_alert)
  4. Portal aksiyonu (Hazırlandı/Delivered veya Returned) başarısız olduğunda uyarı ("qv_action.py" -> send_action_failure_alert)
  5. 7/24 Sistem Sağlık ve Performans/Yük Denetimi raporu ("tests/system_check.py" -> send_health_report)

Hotmail / Outlook Spam Koruması Kuralları:
  - From: "Quietvox Takip Sistemi <SMTP_USER>"
  - Reply-To: SMTP_USER
  - Message-ID: email.utils.make_msgid(domain="gmail.com")
  - Date: email.utils.formatdate(localtime=True)
  - Precedence: bulk veya X-Priority gibi spam filtresi tetikleyen başlıklar EKLENMEZ.
  - Multipart/Alternative: önce düz metin (text/plain), ardından sade kurumsal HTML (text/html).
  - Konu satırlarında emoji ve tamamı BÜYÜK HARF ifadeler kullanılmaz.
"""
from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Istanbul")
ORDER_RE = re.compile(r"^(\d{4,15}):(\d{4}-\d{2}-\d{2})$")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_ORDERS = 200
COLUMNS = ("QV ID", "Otel", "Acente", "Kişi", "Ekipman", "Teslim", "İade", "İşlem saati")

# Spam filtresi tetiklemeyen standart konu satırları
SUBJECT_HEALTH_OK = "Quietvox Sistem Raporu - Tum Sistemler Aktif"
SUBJECT_HEALTH_WARN = "Quietvox Sistem Uyarisi - Kontrol Gerekiyor"
SUBJECT_FORM_PRINTED = "Quietvox Siparis Formu Basildi ve Teslim Edildi"
SUBJECT_APP_UPDATED = "Quietvox Uygulama Guncellemesi Yayinlandi"
SUBJECT_LOGIN_ALERT = "Quietvox Oturum Uyarisi - Sifre Kontrolu Gerekiyor"

# Geriye dönük sabit isimleri
ALERT_SUBJECT = SUBJECT_LOGIN_ALERT
UPDATE_SUBJECT = SUBJECT_APP_UPDATED
DEFAULT_APP_URL = "https://burakkayadev.github.io/qv-takip-b5d52d35/"


def _extract_email(sender: str) -> str:
    match = re.search(r"<([^>]+)>", sender or "")
    return (match.group(1) if match else (sender or "")).strip()


def format_from_header(user: str) -> str:
    addr = _extract_email(user)
    if not addr:
        return "Quietvox Takip Sistemi"
    return f"Quietvox Takip Sistemi <{addr}>"


def create_rfc_message(subject: str, sender_user: str, recipients: list[str],
                       text_body: str, html_body: str) -> EmailMessage:
    """Hotmail/Outlook uyumlu, RFC standartlarında multipart/alternative e-posta oluşturur."""
    addr = _extract_email(sender_user)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = format_from_header(addr)
    msg["To"] = ", ".join(recipients)
    if addr:
        msg["Reply-To"] = addr
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="gmail.com")
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    return msg


def parse_orders(text: str) -> list[tuple[str, str]]:
    """'id:tarih,...' metnini doğrular; geçersiz parçalar atlanır, tekrarlar tekilleştirilir."""
    seen: dict[str, str] = {}
    for part in (text or "").split(","):
        match = ORDER_RE.match(part.strip())
        if match and match.group(1) not in seen:
            seen[match.group(1)] = match.group(2)
    return list(seen.items())[:MAX_ORDERS]


def _booking_id(order: dict) -> str:
    form = order.get("dr_form") or {}
    return str(form.get("qv_booking_id") or str(order.get("id", "")).split("_")[0])


def _ddmmyyyy(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}" if ISO_RE.match(iso or "") else (iso or "—")


def _equipment_text(order: dict) -> str:
    """Ekipman özeti: RX/TX adetleri ve toplam kalem sayısı."""
    items = (order.get("dr_form") or {}).get("equipment") or []
    counts: dict[str, int] = {}
    for row in items:
        name, qty = str(row.get("item", "")), str(row.get("quantity", ""))
        if not qty.isdigit():
            continue
        for tag in ("RX", "TX"):
            if re.search(rf"\b{tag}\b", name):
                counts[tag] = counts.get(tag, 0) + int(qty)
    parts = [f"{tag} {counts[tag]}" for tag in ("RX", "TX") if tag in counts]
    if parts:
        return ", ".join(parts) + f" ({len(items)} kalem)"
    return f"{len(items)} kalem" if items else "—"


def build_rows(orders: list[tuple[str, str]], data: dict, printed_at: datetime) -> list[list[str]]:
    """Her sipariş için tablo satırı; data.json'da bulunamayan sipariş yine kimliğiyle listelenir."""
    deliveries = data.get("deliveries") or []
    returns = data.get("returns") or []
    stamp = printed_at.astimezone(TZ).strftime("%d.%m.%Y %H:%M")
    rows = []
    for order_id, day in orders:
        delivery = next((o for o in deliveries if _booking_id(o) == order_id and o.get("date") == day), None) \
            or next((o for o in deliveries if _booking_id(o) == order_id), None)
        ret = next((o for o in returns if _booking_id(o) == order_id), None)
        source = delivery or ret or {}
        waybill = source.get("waybill") or {}
        form = source.get("dr_form") or {}
        rows.append([
            order_id,
            source.get("location_name") or source.get("title") or "—",
            waybill.get("client") or form.get("customer") or "—",
            form.get("clients") or "—",
            _equipment_text(source),
            _ddmmyyyy((delivery or {}).get("date") or day),
            _ddmmyyyy((ret or {}).get("date") or waybill.get("tour_end", "")),
            stamp,
        ])
    return rows


def build_subject(count: int = 1) -> str:
    return SUBJECT_FORM_PRINTED


def build_text(rows: list[list[str]]) -> str:
    lines = [f"{len(rows)} sipariş için Delivery & Return Formu basıldı ve Quietvox'a Delivered olarak gönderildi.", ""]
    lines += [" | ".join(f"{col}: {val}" for col, val in zip(COLUMNS, row)) for row in rows]
    lines += ["", "Bu e-posta Quietvox Takip Sistemi tarafından otomatik gönderilmiştir."]
    return "\n".join(lines)


def build_html(rows: list[list[str]]) -> str:
    cell = "border:1px solid #d1d5db;padding:7px 10px"
    head = "".join(
        f'<th style="{cell};background:#1e293b;color:#ffffff;text-align:left">{escape(c)}</th>'
        for c in COLUMNS
    )
    body = "".join(
        "<tr>" + "".join(f'<td style="{cell}">{escape(str(v))}</td>' for v in row) + "</tr>"
        for row in rows
    )
    return (
        '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2937;line-height:1.5">'
        f"<p>{len(rows)} sipariş için <b>Delivery &amp; Return Formu</b> basıldı ve Quietvox sistemine "
        "<b>Delivered</b> olarak gönderildi.</p>"
        '<table style="border-collapse:collapse;font-family:Segoe UI,Arial,sans-serif;font-size:13px;width:100%">'
        f"<tr>{head}</tr>{body}</table>"
        '<p style="color:#6b7280;font-size:12px;margin-top:16px">'
        "Bu e-posta Quietvox Takip Sistemi tarafından otomatik gönderilmiştir.</p></div>"
    )


def build_message(rows: list[list[str]], sender: str, recipients: list[str]) -> EmailMessage:
    return create_rfc_message(
        subject=build_subject(len(rows)),
        sender_user=sender,
        recipients=recipients,
        text_body=build_text(rows),
        html_body=build_html(rows),
    )


def send(message: EmailMessage, host: str, port: int, user: str, password: str) -> None:
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as smtp:
            smtp.login(user, password)
            smtp.send_message(message)
        return
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls(context=context)
        smtp.login(user, password)
        smtp.send_message(message)


def smtp_config(env, recipients_var: str = "MAIL_TO") -> tuple[list[str], str, str, str, int] | None:
    """(alıcılar, kullanıcı, şifre, sunucu, port); eksik bilgi varsa None."""
    raw = env.get(recipients_var, "") or env.get("MAIL_TO", "")
    recipients = [a.strip() for a in raw.split(",") if a.strip()]
    user, password = (env.get("SMTP_USER") or "").strip(), env.get("SMTP_PASS") or ""
    if not (recipients and user and password):
        return None
    host = (env.get("SMTP_HOST") or "").strip() or "smtp.gmail.com"
    port = int((env.get("SMTP_PORT") or "").strip() or 465)
    return recipients, user, password, host, port


# ---- Quietvox giriş hatası (şifre değişmiş olabilir) uyarısı -------------------------------

def build_alert_steps(app_url: str) -> list[str]:
    return [
        "Telefonda Quietvox uygulamasını açın" + (f" ({app_url})" if app_url else "") + ".",
        "Üstteki \"Quietvox oturumu güncellenmeli\" uyarısına dokunun "
        "(ya da Menü -> Quietvox bağlantısı).",
        "\"Quietvox Şifresini Güncelle\" kutusuna yeni şifreyi yazıp \"Kaydet ve Dene\" düğmesine basın.",
        "Birkaç dakika içinde \"Yeni şifre çalışıyor\" bilgisi görünür ve veriler kendiliğinden güncellenir.",
    ]


def build_alert_text(failed_at: datetime, app_url: str) -> str:
    when = failed_at.astimezone(TZ).strftime("%d.%m.%Y %H:%M")
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(build_alert_steps(app_url), 1))
    return (
        f"Quietvox sistemine {when} (İstanbul saati) itibarıyla giriş yapılamadı. "
        "Şifre değişmiş olabilir.\n\n"
        "Uygulama son kaydedilen verilerle çalışmaya devam ediyor; hiçbir sipariş silinmedi.\n\n"
        f"Yeni şifreyi 10 saniyede güncelleyin:\n{steps}\n\n"
        "Bilgisayardan: GitHub repo -> Settings -> Secrets and variables -> Actions -> QV_PASS -> Update.\n"
        "Sorun sürerse bu uyarı en fazla saatte bir tekrarlanır.\n"
    )


def build_alert_html(failed_at: datetime, app_url: str) -> str:
    when = failed_at.astimezone(TZ).strftime("%d.%m.%Y %H:%M")
    steps = "".join(f"<li>{escape(s)}</li>" for s in build_alert_steps(app_url))
    return (
        '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2937;line-height:1.5">'
        f"<p><b>Quietvox sistemine {escape(when)} (İstanbul saati) itibarıyla giriş yapılamadı.</b> "
        "Şifre değişmiş olabilir.</p>"
        "<p>Uygulama son kaydedilen verilerle çalışmaya devam ediyor; hiçbir sipariş silinmedi.</p>"
        f"<p><b>Yeni şifreyi 10 saniyede güncelleyin:</b></p><ol>{steps}</ol>"
        "<p>Bilgisayardan: GitHub repo &rarr; Settings &rarr; Secrets and variables &rarr; Actions &rarr; "
        "<code>QV_PASS</code> &rarr; Update.</p>"
        '<p style="color:#6b7280;font-size:12px">Sorun sürerse bu uyarı en fazla saatte bir tekrarlanır.</p></div>'
    )


def send_login_alert(failed_at: datetime, env, app_url: str = "") -> bool:
    """Giriş hatası e-postasını gönderir. SMTP/alıcı tanımlı değilse False (hata vermez)."""
    config = smtp_config(env, "ALERT_TO")
    if config is None:
        print("ALERT_TO/MAIL_TO veya SMTP bilgisi yok; giriş hatası e-postası gönderilmedi.")
        return False
    recipients, user, password, host, port = config
    message = create_rfc_message(
        subject=SUBJECT_LOGIN_ALERT,
        sender_user=user,
        recipients=recipients,
        text_body=build_alert_text(failed_at, app_url),
        html_body=build_alert_html(failed_at, app_url),
    )
    send(message, host, port, user, password)
    return True


# ---- Portal Aksiyonu (Hazırlandı / Teslim Alındı) Başarısızlık Uyarısı ----------------------

def send_action_failure_alert(failures: list[tuple[str, str]], action_label: str, env) -> bool:
    """Portalda durum güncellemesi 2 yeniden denemeye rağmen başarısız olursa uyarı maili gönderir."""
    config = smtp_config(env, "MAIL_TO")
    if config is None or not failures:
        return False
    recipients, user, password, host, port = config
    when = datetime.now(timezone.utc).astimezone(TZ).strftime("%d.%m.%Y %H:%M")
    app_url = (env.get("APP_URL") or DEFAULT_APP_URL).strip()

    text_lines = [
        f"Quietvox portalında {len(failures)} sipariş için '{action_label}' işlemi otomatik denemelere rağmen tamamlanamadı.",
        f"Tarih / Saat: {when} (İstanbul)",
        "",
        "Etkilenen Siparişler:",
    ]
    for oid, reason in failures:
        text_lines.append(f"- Sipariş {oid}: {reason}")
    text_lines += [
        "",
        f"Uygulama üzerinden 'Tekrar Dene' düğmesiyle yeniden gönderebilirsiniz: {app_url}",
    ]

    rows_html = "".join(
        f'<tr><td style="border:1px solid #d1d5db;padding:7px 10px"><b>{escape(oid)}</b></td>'
        f'<td style="border:1px solid #d1d5db;padding:7px 10px">{escape(reason)}</td></tr>'
        for oid, reason in failures
    )
    html_body = (
        '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2937;line-height:1.5">'
        f"<h3 style=\"color:#1e293b;margin:0 0 10px\">Quietvox İşlem Uyarısı ({escape(action_label)})</h3>"
        f"<p>Quietvox portalında <b>{len(failures)} sipariş</b> için durum güncellemesi otomatik denemelere "
        f"rağmen tamamlanamadı ({escape(when)}).</p>"
        '<table style="border-collapse:collapse;width:100%;font-size:13px;margin:12px 0">'
        '<tr><th style="border:1px solid #d1d5db;padding:7px 10px;background:#1e293b;color:#fff;text-align:left">QV ID</th>'
        '<th style="border:1px solid #d1d5db;padding:7px 10px;background:#1e293b;color:#fff;text-align:left">Hata Açıklaması</th></tr>'
        f"{rows_html}</table>"
        f'<p><a href="{escape(app_url)}" style="color:#2563eb;font-weight:bold">Uygulamayı Aç ({escape(app_url)})</a></p>'
        "</div>"
    )
    message = create_rfc_message(
        subject=SUBJECT_HEALTH_WARN,
        sender_user=user,
        recipients=recipients,
        text_body="\n".join(text_lines),
        html_body=html_body,
    )
    send(message, host, port, user, password)
    return True


# ---- Uygulama Güncellemesi Bildirim E-postası (".github/workflows/version.yml" çağırır) -----

def build_update_text(version_data: dict, commit_msg: str, app_url: str, updated_at: datetime) -> str:
    when = updated_at.astimezone(TZ).strftime("%d.%m.%Y %H:%M")
    ver = version_data.get("version") or "4.0.0"
    build = version_data.get("build") or "—"
    commit = version_data.get("commit") or "—"
    summary = (commit_msg or "Uygulama iyileştirmeleri ve hata düzeltmeleri").strip()
    url = (app_url or DEFAULT_APP_URL).strip()
    return (
        "Quietvox Takip Uygulaması yeni sürüme güncellendi ve yayına alındı.\n\n"
        f"- Güncelleme Saati (İstanbul): {when}\n"
        f"- Sürüm Bilgisi: v{ver} (Build #{build} - Commit {commit})\n"
        f"- Son Değişiklik Özeti:\n{summary}\n\n"
        f"- Canlı Uygulama Linki (doğrudan açıp yenileyebilirsiniz):\n{url}\n\n"
        "Bu e-posta Quietvox Takip Sistemi tarafından yalnızca yeni uygulama güncellemesi "
        "yayına alındığında otomatik gönderilmiştir.\n"
    )


def build_update_html(version_data: dict, commit_msg: str, app_url: str, updated_at: datetime) -> str:
    when = updated_at.astimezone(TZ).strftime("%d.%m.%Y %H:%M")
    ver = escape(str(version_data.get("version") or "4.0.0"))
    build = escape(str(version_data.get("build") or "—"))
    commit = escape(str(version_data.get("commit") or "—"))
    summary = escape((commit_msg or "Uygulama iyileştirmeleri ve hata düzeltmeleri").strip()).replace("\n", "<br>")
    url = escape((app_url or DEFAULT_APP_URL).strip())
    return (
        '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2937;max-width:620px;line-height:1.5">'
        '<h2 style="color:#1e293b;margin:0 0 12px;font-size:18px">Quietvox Uygulama Güncellemesi Yayınlandı</h2>'
        '<p>Uygulamanın yeni sürümü yayına alındı. Aşağıdaki bağlantıya dokunarak doğrudan açıp kullanabilirsiniz:</p>'
        '<table style="border-collapse:collapse;width:100%;margin:12px 0;font-size:13px">'
        f'<tr><td style="border:1px solid #d1d5db;padding:8px 10px;background:#f8fafc;width:165px"><b>Güncelleme Saati</b></td>'
        f'<td style="border:1px solid #d1d5db;padding:8px 10px">{escape(when)} (İstanbul)</td></tr>'
        f'<tr><td style="border:1px solid #d1d5db;padding:8px 10px;background:#f8fafc"><b>Sürüm Bilgisi</b></td>'
        f'<td style="border:1px solid #d1d5db;padding:8px 10px">v{ver} &middot; Build #{build} (<code>{commit}</code>)</td></tr>'
        f'<tr><td style="border:1px solid #d1d5db;padding:8px 10px;background:#f8fafc"><b>Son Değişiklik Özeti</b></td>'
        f'<td style="border:1px solid #d1d5db;padding:8px 10px">{summary}</td></tr>'
        f'<tr><td style="border:1px solid #d1d5db;padding:8px 10px;background:#f8fafc"><b>Canlı Uygulama</b></td>'
        f'<td style="border:1px solid #d1d5db;padding:8px 10px"><a href="{url}" style="color:#2563eb;font-weight:bold">{url}</a></td></tr>'
        '</table>'
        f'<p style="margin:16px 0"><a href="{url}" style="background:#1e40af;color:#ffffff;padding:10px 18px;'
        'border-radius:6px;text-decoration:none;font-weight:bold;display:inline-block">'
        'Uygulamayı Aç ve Yenile</a></p>'
        '<p style="color:#6b7280;font-size:12px">Bu e-posta Quietvox Takip Sistemi tarafından yalnızca yeni sürüm '
        'yayına alındığında otomatik gönderilir (otomatik veri çekimlerinde gönderilmez).</p></div>'
    )


def send_update_notification(env, version_path: Path | None = None) -> bool:
    """Yeni uygulama sürümü yayına alındığında bilgilendirme e-postası gönderir."""
    config = smtp_config(env, "MAIL_TO")
    if config is None:
        print("MAIL_TO / SMTP_USER / SMTP_PASS tanımlı değil; güncelleme e-postası gönderilmedi.")
        return False
    recipients, user, password, host, port = config
    vpath = version_path or Path(__file__).with_name("version.json")
    try:
        version_data = json.loads(vpath.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        version_data = {"version": "4.0.0"}
    try:
        raw_dt = str(version_data.get("updated_at") or "").replace("Z", "+00:00")
        updated_at = datetime.fromisoformat(raw_dt)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
    except ValueError:
        updated_at = datetime.now(timezone.utc)
    commit_msg = env.get("COMMIT_MESSAGE") or ""
    app_url = env.get("APP_URL") or DEFAULT_APP_URL

    message = create_rfc_message(
        subject=SUBJECT_APP_UPDATED,
        sender_user=user,
        recipients=recipients,
        text_body=build_update_text(version_data, commit_msg, app_url, updated_at),
        html_body=build_update_html(version_data, commit_msg, app_url, updated_at),
    )
    send(message, host, port, user, password)
    print(f"Güncelleme bildirim e-postası gönderildi: build #{version_data.get('build')}, {len(recipients)} alıcı.")
    return True


# ---- 7/24 Sistem Sağlık ve Performans/Yük Denetimi Raporu E-postası -------------------------

def send_health_report(report: dict, env) -> bool:
    """tests/system_check.py tarafından üretilen sağlık ve yük denetimi raporunu gönderir."""
    config = smtp_config(env, "MAIL_TO")
    if config is None:
        print("MAIL_TO / SMTP_USER / SMTP_PASS tanımlı değil; sağlık raporu e-postası gönderilmedi.")
        return False
    recipients, user, password, host, port = config
    healthy = bool(report.get("healthy", True))
    subject = SUBJECT_HEALTH_OK if healthy else SUBJECT_HEALTH_WARN
    checked_at = report.get("checked_at") or datetime.now(timezone.utc).astimezone(TZ).strftime("%d.%m.%Y %H:%M")
    app_url = report.get("app_url") or DEFAULT_APP_URL
    checks = report.get("checks") or []
    optimizations = report.get("optimizations") or []
    metrics = report.get("metrics") or {}

    text_lines = [
        f"Quietvox Sistem Sağlık ve Performans Raporu ({checked_at} İstanbul)",
        f"Genel Durum: {'TÜM SİSTEMLER AKTİF' if healthy else 'KONTROL GEREKİYOR'}",
        "",
        "1. Sistem Kontrolleri:",
    ]
    for item in checks:
        status_label = "OK" if item.get("ok") else "UYARI"
        text_lines.append(f"- [{status_label}] {item.get('name')}: {item.get('detail')}")

    if metrics:
        text_lines += [
            "",
            "2. Performans ve Boyut Ölçümleri:",
            f"- index.html Boyutu: {metrics.get('index_kb', '—')} KB ({metrics.get('index_lines', '—')} satır)",
            f"- data.json Boyutu: {metrics.get('data_kb', '—')} KB "
            f"({metrics.get('deliveries_count', 0)} teslimat, {metrics.get('returns_count', 0)} iade)",
            f"- Son Veri Güncellemesi: {metrics.get('data_age_min', '—')} dk önce",
        ]

    text_lines += ["", "3. Gereksiz Yük / Optimizasyon Önerisi:"]
    if optimizations:
        for opt in optimizations:
            text_lines.append(f"- {opt}")
    else:
        text_lines.append("- Sistemde kritik şişkinlik veya gereksiz yük tespit edilmedi.")

    text_lines += [
        "",
        f"Canlı Uygulama: {app_url}",
        "Bu e-posta Quietvox Takip Sistemi sağlık kontrolü tarafından otomatik gönderilmiştir.",
    ]

    check_rows = "".join(
        '<tr>'
        f'<td style="border:1px solid #d1d5db;padding:7px 10px"><b>{escape(str(item.get("name", "")))}</b></td>'
        f'<td style="border:1px solid #d1d5db;padding:7px 10px;color:{"#15803d" if item.get("ok") else "#b91c1c"};font-weight:bold">'
        f'{"Aktif / Sorunsuz" if item.get("ok") else "Kontrol Gerekli"}</td>'
        f'<td style="border:1px solid #d1d5db;padding:7px 10px">{escape(str(item.get("detail", "")))}</td>'
        '</tr>'
        for item in checks
    )
    opt_items = "".join(f"<li style=\"margin-bottom:6px\">{escape(str(opt))}</li>" for opt in optimizations) \
        or "<li>Sistemde kritik şişkinlik veya gereksiz yük tespit edilmedi.</li>"

    html_body = (
        '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2937;max-width:680px;line-height:1.5">'
        f'<h2 style="color:#1e293b;margin:0 0 8px;font-size:18px">{escape(subject)}</h2>'
        f'<p style="margin:0 0 12px;color:#4b5563">Kontrol Zamanı: <b>{escape(str(checked_at))} (İstanbul)</b></p>'
        '<table style="border-collapse:collapse;width:100%;font-size:13px;margin:12px 0">'
        '<tr>'
        '<th style="border:1px solid #d1d5db;padding:7px 10px;background:#1e293b;color:#fff;text-align:left">Kontrol Kalemi</th>'
        '<th style="border:1px solid #d1d5db;padding:7px 10px;background:#1e293b;color:#fff;text-align:left">Durum</th>'
        '<th style="border:1px solid #d1d5db;padding:7px 10px;background:#1e293b;color:#fff;text-align:left">Detay</th>'
        '</tr>'
        f'{check_rows}</table>'
        '<h3 style="color:#1e293b;font-size:15px;margin:16px 0 8px">Gereksiz Yük / Optimizasyon Önerisi</h3>'
        f'<ul style="margin:0 0 14px;padding-left:20px;font-size:13px">{opt_items}</ul>'
        f'<p><a href="{escape(app_url)}" style="color:#2563eb;font-weight:bold">Canlı Uygulamayı Aç ({escape(app_url)})</a></p>'
        '<p style="color:#6b7280;font-size:12px;margin-top:16px">'
        'Bu rapor günde 3 kez (08:00, 13:00, 20:00) ve uygulama güncellemelerinde otomatik gönderilir.</p></div>'
    )

    message = create_rfc_message(
        subject=subject,
        sender_user=user,
        recipients=recipients,
        text_body="\n".join(text_lines),
        html_body=html_body,
    )
    send(message, host, port, user, password)
    print(f"Sağlık raporu e-postası gönderildi ({subject}): {len(recipients)} alıcı.")
    return True


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if "--update" in args or (os.environ.get("NOTIFY_MODE") or "").strip().lower() == "update":
        send_update_notification(os.environ)
        return 0
    orders = parse_orders(os.environ.get("ORDERS", ""))
    if not orders:
        print("Geçerli sipariş yok; e-posta gönderilmedi.")
        return 0
    try:
        printed_at = datetime.fromisoformat(os.environ.get("PRINTED_AT", "").replace("Z", "+00:00"))
        if printed_at.tzinfo is None:
            printed_at = printed_at.replace(tzinfo=timezone.utc)
    except ValueError:
        printed_at = datetime.now(timezone.utc)
    data = json.loads(Path(__file__).with_name("data.json").read_text(encoding="utf-8"))
    rows = build_rows(orders, data, printed_at)
    config = smtp_config(os.environ)
    if config is None:
        print("MAIL_TO / SMTP_USER / SMTP_PASS tanımlı değil; e-posta gönderilmedi (README: E-posta bildirimi).")
        return 0
    recipients, user, password, host, port = config
    send(build_message(rows, user, recipients), host, port, user, password)
    print(f"E-posta gönderildi: {len(rows)} sipariş, {len(recipients)} alıcı.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
