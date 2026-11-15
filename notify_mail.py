#!/usr/bin/env python3
"""Form basıldığında bilgilendirme e-postası gönderir (".github/workflows/notify.yml" çağırır).

Girdiler (ortam değişkenleri):
  ORDERS      "QVID:YYYY-AA-GG,QVID:YYYY-AA-GG" (uygulama gönderir; yalnızca kimlik ve tarih)
  PRINTED_AT  Form basılma zamanı (ISO 8601, UTC)
  MAIL_TO     Alıcılar, virgülle ayrılmış (GitHub Secret; repoda adres tutulmaz)
  SMTP_USER / SMTP_PASS  Gönderen hesap ve uygulama şifresi (GitHub Secret)
  SMTP_HOST / SMTP_PORT  İsteğe bağlı; varsayılan smtp.gmail.com:465

Sipariş ayrıntıları (otel, acente, kişi, ekipman, tarihler) repodaki data.json'dan okunur.
SMTP bilgisi tanımlı değilse e-posta gönderilmez, iş akışı hata vermeden biter.
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
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Istanbul")
ORDER_RE = re.compile(r"^(\d{4,15}):(\d{4}-\d{2}-\d{2})$")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_ORDERS = 200
COLUMNS = ("QV ID", "Otel", "Acente", "Kişi", "Ekipman", "Teslim", "İade", "İşlem saati")


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


def build_subject(count: int) -> str:
    return f"Quietvox: {count} Sipariş Formu Basıldı ve Teslim Edildi (Delivered)"


def build_text(rows: list[list[str]]) -> str:
    lines = [f"{len(rows)} sipariş için Delivery & Return Formu basıldı ve Quietvox'a Delivered olarak gönderildi.", ""]
    lines += [" | ".join(f"{col}: {val}" for col, val in zip(COLUMNS, row)) for row in rows]
    lines += ["", "Bu e-posta Quietvox Takip uygulaması tarafından otomatik gönderildi."]
    return "\n".join(lines)


def build_html(rows: list[list[str]]) -> str:
    cell = "border:1px solid #ccc;padding:6px 8px"
    head = "".join(f'<th style="{cell};background:#1B2030;color:#fff;text-align:left">{escape(c)}</th>' for c in COLUMNS)
    body = "".join("<tr>" + "".join(f'<td style="{cell}">{escape(str(v))}</td>' for v in row) + "</tr>" for row in rows)
    return (f"<p>{len(rows)} sipariş için <b>Delivery &amp; Return Formu</b> basıldı ve Quietvox'a "
            "<b>Delivered</b> olarak gönderildi.</p>"
            '<table style="border-collapse:collapse;font-family:Arial,sans-serif;font-size:14px">'
            f"<tr>{head}</tr>{body}</table>"
            '<p style="color:#666;font-size:12px">Bu e-posta Quietvox Takip uygulaması tarafından otomatik gönderildi.</p>')


def build_message(rows: list[list[str]], sender: str, recipients: list[str]) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = build_subject(len(rows))
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(build_text(rows))
    message.add_alternative(build_html(rows), subtype="html")
    return message


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


# ---- Quietvox giriş hatası (şifre değişmiş olabilir) acil uyarısı -------------------------
ALERT_SUBJECT = "⚠️ ACİL: Quietvox Şifresi Değişmiş Olabilir - Giriş Başarısız"


def build_alert_steps(app_url: str) -> list[str]:
    return [
        "Telefonda Quietvox uygulamasını açın" + (f" ({app_url})" if app_url else "") + ".",
        "Üstteki kırmızı \"⚠️ Quietvox oturumu güncellenmeli\" uyarısına dokunun "
        "(ya da ☰ menü → ⚙ Quietvox bağlantısı).",
        "\"🔑 Quietvox Şifresini Güncelle\" kutusuna yeni şifreyi yazıp \"Kaydet ve Dene\"ye basın.",
        "Birkaç dakika içinde \"✅ Yeni şifre çalışıyor\" yazısı çıkar; veriler kendiliğinden güncellenir.",
    ]


def build_alert_text(failed_at: datetime, app_url: str) -> str:
    when = failed_at.astimezone(TZ).strftime("%d.%m.%Y %H:%M")
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(build_alert_steps(app_url), 1))
    return (
        f"Quietvox sistemine {when} (İstanbul saati) itibarıyla giriş yapılamadı. "
        "Şifre değişmiş olabilir.\n\n"
        "Uygulama son kaydedilen verilerle çalışmaya devam ediyor; hiçbir sipariş silinmedi.\n\n"
        f"Yeni şifreyi 10 saniyede güncelleyin:\n{steps}\n\n"
        "Bilgisayardan: GitHub repo → Settings → Secrets and variables → Actions → QV_PASS → Update.\n"
        "Sorun sürerse bu uyarı en fazla saatte bir tekrarlanır.\n"
    )


def build_alert_html(failed_at: datetime, app_url: str) -> str:
    when = failed_at.astimezone(TZ).strftime("%d.%m.%Y %H:%M")
    steps = "".join(f"<li>{escape(s)}</li>" for s in build_alert_steps(app_url))
    return (
        '<div style="font-family:Arial,sans-serif;font-size:14px;color:#111">'
        f"<p><b>Quietvox sistemine {escape(when)} (İstanbul saati) itibarıyla giriş yapılamadı.</b> "
        "Şifre değişmiş olabilir.</p>"
        "<p>Uygulama son kaydedilen verilerle çalışmaya devam ediyor; hiçbir sipariş silinmedi.</p>"
        f"<p><b>Yeni şifreyi 10 saniyede güncelleyin:</b></p><ol>{steps}</ol>"
        "<p>Bilgisayardan: GitHub repo → Settings → Secrets and variables → Actions → "
        "<code>QV_PASS</code> → Update.</p>"
        '<p style="color:#666">Sorun sürerse bu uyarı en fazla saatte bir tekrarlanır.</p></div>'
    )


def send_login_alert(failed_at: datetime, env, app_url: str = "") -> bool:
    """Giriş hatası e-postasını gönderir. SMTP/alıcı tanımlı değilse False (hata vermez).

    Alıcılar ALERT_TO (tanımlıysa) yoksa MAIL_TO Secret'ından okunur.
    """
    config = smtp_config(env, "ALERT_TO")
    if config is None:
        print("ALERT_TO/MAIL_TO veya SMTP bilgisi yok; giriş hatası e-postası gönderilmedi.")
        return False
    recipients, user, password, host, port = config
    message = EmailMessage()
    message["Subject"] = ALERT_SUBJECT
    message["From"] = user
    message["To"] = ", ".join(recipients)
    message["X-Priority"] = "1"
    message.set_content(build_alert_text(failed_at, app_url))
    message.add_alternative(build_alert_html(failed_at, app_url), subtype="html")
    send(message, host, port, user, password)
    return True


# ---- Uygulama güncellemesi bildirim e-postası (".github/workflows/version.yml" çağırır) ------
UPDATE_SUBJECT = "🚀 Quietvox Takip Uygulaması Güncellendi (Yeni Sürüm Hazır)"
DEFAULT_APP_URL = "https://burakkayadev.github.io/qv-takip-b5d52d35/"


def build_update_text(version_data: dict, commit_msg: str, app_url: str, updated_at: datetime) -> str:
    when = updated_at.astimezone(TZ).strftime("%d.%m.%Y %H:%M")
    ver = version_data.get("version") or "4.0.0"
    build = version_data.get("build") or "—"
    commit = version_data.get("commit") or "—"
    summary = (commit_msg or "Uygulama iyileştirmeleri ve hata düzeltmeleri").strip()
    url = (app_url or DEFAULT_APP_URL).strip()
    return (
        "Quietvox Takip Uygulaması yeni sürüme güncellendi ve yayına alındı.\n\n"
        f"• Güncelleme Saati (İstanbul): {when}\n"
        f"• Sürüm Bilgisi: v{ver} (Build #{build} · Commit {commit})\n"
        f"• Son Değişiklik Özeti:\n{summary}\n\n"
        f"• Canlı Uygulama Linki (doğrudan açıp yenileyebilirsiniz):\n{url}\n\n"
        "Bu e-posta Quietvox Takip güncelleme sistemi tarafından otomatik gönderildi.\n"
    )


def build_update_html(version_data: dict, commit_msg: str, app_url: str, updated_at: datetime) -> str:
    when = updated_at.astimezone(TZ).strftime("%d.%m.%Y %H:%M")
    ver = escape(str(version_data.get("version") or "4.0.0"))
    build = escape(str(version_data.get("build") or "—"))
    commit = escape(str(version_data.get("commit") or "—"))
    summary = escape((commit_msg or "Uygulama iyileştirmeleri ve hata düzeltmeleri").strip()).replace("\n", "<br>")
    url = escape((app_url or DEFAULT_APP_URL).strip())
    return (
        '<div style="font-family:Arial,sans-serif;font-size:14px;color:#111;max-width:600px">'
        '<h2 style="color:#1B2030;margin:0 0 12px">🚀 Quietvox Takip Uygulaması Güncellendi</h2>'
        '<p>Uygulamanın yeni sürümü yayına alındı. Aşağıdaki bağlantıya dokunarak doğrudan açıp kullanabilirsiniz:</p>'
        '<table style="border-collapse:collapse;width:100%;margin:12px 0;font-size:14px">'
        f'<tr><td style="border:1px solid #ddd;padding:8px;background:#f8fafc;width:160px"><b>Güncelleme Saati</b></td>'
        f'<td style="border:1px solid #ddd;padding:8px">{escape(when)} (İstanbul)</td></tr>'
        f'<tr><td style="border:1px solid #ddd;padding:8px;background:#f8fafc"><b>Sürüm Bilgisi</b></td>'
        f'<td style="border:1px solid #ddd;padding:8px">v{ver} · Build #{build} (<code>{commit}</code>)</td></tr>'
        f'<tr><td style="border:1px solid #ddd;padding:8px;background:#f8fafc"><b>Son Değişiklik Özeti</b></td>'
        f'<td style="border:1px solid #ddd;padding:8px">{summary}</td></tr>'
        f'<tr><td style="border:1px solid #ddd;padding:8px;background:#f8fafc"><b>Canlı Uygulama</b></td>'
        f'<td style="border:1px solid #ddd;padding:8px"><a href="{url}" style="color:#2563eb;font-weight:bold">{url}</a></td></tr>'
        '</table>'
        f'<p style="margin:18px 0"><a href="{url}" style="background:#2563eb;color:#fff;padding:10px 18px;'
        'border-radius:8px;text-decoration:none;font-weight:bold;display:inline-block">'
        '📱 Uygulamayı Aç ve Yenile</a></p>'
        '<p style="color:#666;font-size:12px">Bu e-posta Quietvox Takip uygulaması tarafından yalnızca yeni sürüm '
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

    message = EmailMessage()
    message["Subject"] = UPDATE_SUBJECT
    message["From"] = user
    message["To"] = ", ".join(recipients)
    message.set_content(build_update_text(version_data, commit_msg, app_url, updated_at))
    message.add_alternative(build_update_html(version_data, commit_msg, app_url, updated_at), subtype="html")
    send(message, host, port, user, password)
    print(f"Güncelleme bildirim e-postası gönderildi: build #{version_data.get('build')}, {len(recipients)} alıcı.")
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
