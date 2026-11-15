# Antigravity Uygulaması Sistem Kuralı

Sen şu an Antigravity uygulaması üzerinden çalışıyorsun (aktif modelden bağımsız olarak bu kural geçerlidir). Her göreve başlamadan önce `MODEL_BRIDGE.md` dosyasını oku ve Claude Masaüstü uygulamasının yaptığı son değişiklikleri kontrol et. Görev bitiminde `MODEL_BRIDGE.md` dosyasının en üstüne 'Son Aktif Uygulama: Antigravity' başlığıyla yaptığın işleri kaydet ve her adım sonunda kullanıcıya çalışan canlı kontrol linki ver.

---

### Sabit Kurallar

> **Zorunlu Canlı Kontrol Linki Kuralı:** İster yerel bir önizleme (`ui-preview.html` / `index.html`) hazırlansın ister GitHub'a `push` yapılsın, her güncelleme ve her işlem adımının sonunda kullanıcıya doğrudan tıklayıp test edebileceği çalışan bir canlı kontrol linki ver (`http://localhost:8080/...` yerel sunucu başlatarak veya `push` sonrası `https://burakkayadev.github.io/qv-takip-b5d52d35/?v=<timestamp>` önbelleksiz canlı linki).

- **Tasarım:** Midnight Obsidian Glass saydam tasarımı ve `@media print` A4 siyah-beyaz izolasyonunu koru.
- **Yedekleme:** Büyük değişiklikler öncesi yedek al ve `.gitignore` dosyasına ekle.
- **Ortak Hafıza:** Görev tamamlandığında `MODEL_BRIDGE.md` dosyasındaki "SON DURUM ÖZETİ" tablosunu güncelle.
