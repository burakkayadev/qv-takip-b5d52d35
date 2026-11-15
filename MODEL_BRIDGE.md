# MODEL_BRIDGE — Uygulamalar Arası Ortak Hafıza

> **Son Aktif Uygulama:** Antigravity  
> **Son Güncelleme:** 15 Kasım 2026, 19:24 (UTC+3)

---

## 📌 SON DURUM ÖZETİ

| Başlık | Detay |
|---|---|
| **Tarih / Saat** | 15.11.2026 19:24 (İstanbul) |
| **Son Aktif Uygulama** | Antigravity |
| **Tamamlanan İşler** | 1. **Takvim Ezilme & Barkod Boş Çizgi Düzeltmesi:** `.cal-order-chip` için `flex-shrink: 0 !important; min-height: 38px !important;`, `.cal-day-cell` için `min-height: 140px; height: auto;` ve `.qv-cal-days` için `minmax(140px, auto)` uygulanarak 12 Kasım gibi 16+ siparişli günlerde çiplerin 2px boş çizgiye küçülmesi tamamen engellendi.<br>2. **Verilecek / Alınacak Yön Ayrımı & Rozetleri:** Her takvim çipinin başına `[📤 VER]` (zümrüt yeşili) veya `[📥 AL]` (mavi) rozeti eklendi. Çift satırlı temiz yapı (Müşteri + Otel/Pax) korundu.<br>3. **Takvim Üst Filtre Butonları:** `[ 🌐 Tümü ] [ 📤 Sadece Verilecekler ] [ 📥 Sadece Alınacaklar ]` butonları dinamik olarak `calState.filter` üzerinden takvimi anında süzüyor.<br>4. **Otel İsmi Olmayan (N/A) Siparişler:** `waybill.delivery_location` / `return_location` ilk satırına otomatik dönüştürüldü; adres de yoksa `'Otel Belirtilmemiş (ID: ...)'` yazılarak boş görünüm tamamen giderildi.<br>5. **Canlı Test & Doğrulama:** 1280px masaüstü ve 390px Fold/mobil cihaz görünümleri Playwright ile test edildi, ekran görüntüleriyle doğrulandı.<br>6. **Sürümleme:** `version.json` v4.0.1 (Build 2) olarak güncellendi. |
| **Değişen Dosyalar** | `index.html`, `version.json`, `MODEL_BRIDGE.md`, `HANDOFF_TO_CLAUDE.md` |
| **Bir Sonraki Uygulamanın Yapacağı İş** | Kullanıcı onayından sonra GitHub push ve Android APK release adımlarını takip etmek. |

---

## 🛡️ SABİT PROJE KURALLARI

1. **Zorunlu Canlı Kontrol Linki Kuralı:**
   > İster yerel bir önizleme (`ui-preview.html` / `index.html`) hazırlansın ister GitHub'a `push` yapılsın, her güncelleme ve her işlem adımının sonunda kullanıcıya doğrudan tıklayıp test edebileceği çalışan bir canlı kontrol linki ver (`http://localhost:8080/...` yerel sunucu başlatarak veya `push` sonrası `https://burakkayadev.github.io/qv-takip-b5d52d35/?v=<timestamp>` önbelleksiz canlı linki).

2. **Ultra-Saydam Midnight Obsidian Glass & `@media print` İzolasyonu:**
   - Arayüzde derin lacivert zemin (`#0B1120` → `#162038`), yumuşak cam derinliği (`backdrop-filter: blur(28px) saturate(190%)`) ve ince saydam kenarlıklar (`1px solid rgba(255, 255, 255, 0.16)`) korunacaktır.
   - `@media print` stilleri cam efektlerinden tamamen izole, net siyah-beyaz A4 formatında kalmalıdır.

3. **Otomatik Sürümleme (`version.json`) ve Yedekleme Kuralı:**
   - Her büyük geliştirme öncesi `_backup_before_.../` klasörüne yedek alınır ve `.gitignore` içinde tutulur.
   - Sürüm güncellemesi `version.json` ve PWA Service Worker `sw.js` üzerinden yönetilir.

4. **Uygulamalar Arası Protokol:**
   - Claude Masaüstü veya Antigravity uygulamalarından hangisi işe başlarsa başlasın önce bu dosyayı okur.
   - İş bittiğinde en üstteki **"Son Aktif Uygulama"** ve **"SON DURUM ÖZETİ"** alanını günceller.
