# MODEL_BRIDGE — Uygulamalar Arası Ortak Hafıza

> **Son Aktif Uygulama:** Claude Masaüstü  
> **Son Güncelleme:** 15 Kasım 2026 (UTC+3) — **GitHub'a gönderildi** (commit `aafe0bc`; sürüm iş akışı build 4'ü `version.json`, `index.html` ve `sw.js`'e birlikte yazdı, canlıda doğrulandı)

---

## 📌 SON DURUM ÖZETİ — Son Aktif Uygulama: Claude Masaüstü

| Başlık | Detay |
|---|---|
| **Son Aktif Uygulama** | Claude Masaüstü |
| **Tamamlanan İşler** | 1. **Takvim — otel adları tam:** `.cal-company` / `.cal-hotel` artık `white-space: normal; line-height: 1.3`; `nowrap`/`ellipsis` kaldırıldı, uzun adlar ("Four Seasons Hotel Istanbul At Sultanahmet") alt satıra kayarak tam okunuyor.<br>2. **Takvim — telefon (< 768px):** varsayılan **Liste** görünümü (bugünden başlayan 7 gün alt alta; her günde **📤 Verilecekler** ve **📥 Alınacaklar** ayrı bloklar, büyük yazı, en küçük yazı 14px). Görünüm anahtarına "Liste" eklendi; Ay görünümü telefonda 150px sütunlarla yatay kaydırmalı. Çip kodu tek işlevde toplandı (`buildCalChip`), Gün görünümü de liste düzenini kullanıyor. Masaüstü varsayılanı Ay olarak kaldı.<br>3. **Sade Mod kaldırıldı:** menüdeki ve Ayarlar penceresindeki anahtar, `buildMinimalCard`, `.card-minimal` CSS'i, `LS_VIEW_MODE` ve `updateViewModeButtons` silindi; telefondaki eski `qv.view_mode.v1` kaydı açılışta temizleniyor.<br>4. **Sesli asistan:** konuşurken mikrofona basınca yalnızca susturuyor ve mikrofonu açmıyor (kendi sesini dinlemez); dinlerken 2. basış komut çalıştırmadan iptal ediyor. Tarih ayrıştırma yeniden yazıldı (`parseVoiceDate`): "ayın 18'inde", "18 Aralık", "on sekiz kasım", "dün", "yarından sonra", hafta günleri ("cuma", "haftaya salı"); ay artık sabit "Kasım" değil. Yön (`voiceDirection`): "teslim alınacak" artık alınacaklar sayılıyor (eskiden verilecek sanılıyordu).<br>5. **Otomatik güncelleme hatası (KRİTİK):** `version.yml` build'i her push'ta `run_number` ile yazıyor, `index.html`'deki `CURRENT_BUILD` ise elle sabitti → bir sonraki push'ta "güncelleme var" bandı güncellemeden sonra da kaybolmayacaktı. Artık iş akışı aynı numarayı `version.json`, `index.html` (`CURRENT_BUILD`) ve `sw.js` (`VERSION`) içine **aynı commit'te** yazıyor; yalnızca uygulama dosyaları değişince çalışıyor, gönderimi 3 kez deniyor.<br>6. **Service worker mesajı:** uygulama `{type:'SKIP_WAITING'}` gönderiyordu, `sw.js` yalnızca `{action:'skipWaiting'}` dinliyordu; ikisi de kabul ediliyor.<br>7. **Doğrulama:** 31 maddelik uçtan uca tarayıcı denetimi (375px + 1280px, service worker açık): konsol hatası yok; Sade Mod kalıntısı yok; takvim Liste/Ay/filtre/gezinme; 8 sesli komut senaryosu; 2. basış davranışları; toplu yazdırma sırası (2 Form → 2 WayBill, taşma yok); güncelleme bandı çıkıyor ve güncellemeden sonra kayboluyor. 123 Python testi geçti. |
| **Değişen Dosyalar** | `index.html`, `sw.js`, `.github/workflows/version.yml`, `.gitignore`, `MODEL_BRIDGE.md` (yedek: `_backup_before_claude_v5/`) |
| **Bir Sonraki Uygulamanın Yapacağı İş** | Kullanıcı "push" yazınca commit + push; ardından `version.yml`'in build numarasını üç dosyaya birlikte yazdığını ve canlıda bandın çıkmadığını kontrol et. `CURRENT_BUILD`'i elle değiştirme. |

### Önceki kayıt — Antigravity (15.11.2026 19:24)

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
