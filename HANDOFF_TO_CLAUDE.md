# MODEL_BRIDGE / HANDOFF_TO_CLAUDE — Quietvox v4.0

> **Son Aktif Uygulama:** Antigravity  
> **Son Güncelleme:** 15 Kasım 2026, 19:12 (UTC+3)

---

## 📌 SON DURUM ÖZETİ

| Başlık | Detay |
|---|---|
| **Tarih / Saat** | 15.11.2026 19:12 (İstanbul) |
| **Son Aktif Uygulama** | Antigravity |
| **Tamamlanan İşler** | 1. **Görünüm Modu (Klasik vs Sade Liste):** `buildMinimalCard` ve `.card-minimal` CSS tam olarak entegre edildi. Tek tıkla açılıp kapanan `⚙️ İşlemler ▾` çekmecesi ve tek odaklı onay butonu sorunsuz çalışıyor.<br>2. **LS_VIEW_MODE & Ağ Hatası Düzeltildi:** Başlangıçta tanımsız değişken hatası giderildi, `data.json` verisi ve tüm sayaçlar (Bugün 2 verilecek, 10 alınacak) eksiksiz ekrana yansıdı.<br>3. **Takvim & Filtre Sekmeleri:** Yoğun gün hücre sıkışması çözüldü, `Tümü \| 📤 Verilecekler \| 📥 Alınacaklar` sekmeleri aktif.<br>4. **Sesli Asistan Düzeltmesi:** Yankı engelleme ve 2. dokunuşta anında susturma devrede.<br>5. **Canlı Test:** Yerel sunucu üzerinde her iki mod, takvim ve ayarlar ekran görüntüleriyle %100 doğrulandı.<br>6. **Kullanım Kılavuzu PDF:** `Quietvox_Kullanim_Kilavuzu.pdf` tam 2 sayfa olarak güncellendi. |
| **Değişen Dosyalar** | `index.html`, `sw.js`, `.gitignore`, `Quietvox_Kullanim_Kilavuzu.pdf`, `MODEL_BRIDGE.md`, `CLAUDE.md`, `.agent/rules/bridge.md` |
| **Bir Sonraki Uygulamanın Yapacağı İş** | Kullanıcı "push" onayını verdiği anda tüm değişiklikleri GitHub'a göndermek ve APK derlemesini tetiklemek. |

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
