# Quietvox Sipariş Takip ve Çift Etiket Yazdırma

Bilgisayara ihtiyaç duymadan, telefondan Quietvox teslimat/iade siparişlerini takip etmek ve
çanta etiketlerini (WayBill + Delivery and Return Form) yazdırmak için ücretsiz bir sistem.

- **`scraper.py`** — GitHub Actions üzerinde 30 dakikada bir çalışır, Quietvox takvimlerini okur, `data.json` üretir.
- **`index.html`** — GitHub Pages'te yayınlanan, telefona uygulama gibi kurulabilen (PWA) arayüz.
- **`.github/workflows/sync.yml`** — Zamanlayıcı (30 dk + her akşam 18:00 TR hatırlatma + elle tetikleme).

```
Quietvox  ──(scraper.py, GitHub Actions)──▶  data.json  ──(GitHub Pages)──▶  Telefon (PWA)
                     │
                     └──▶ ntfy.sh bildirimi (yarın hatırlatması / giriş hatası)
```

> ## ⚠️ Önce okuyun: gizlilik
> Ücretsiz GitHub hesabında GitHub Pages yalnızca **herkese açık (public)** repolarda çalışır.
> Bu durumda `data.json` — yani müşteri adları, rehber adları/telefonları ve otel adresleri —
> **internette herkesin erişebileceği** bir adreste durur. Adres tahmin edilmesi zor olsa da gizli değildir.
> Seçenekler:
> 1. Riski kabul edip public repo kullanmak (repo adını tahmin edilmesi zor seçin, ör. `qv-7h3k9`).
> 2. GitHub Pro ile **private** repo + Pages kullanmak (kod ve geçmiş gizlenir; yayınlanan site yine herkese açıktır).
> 3. Pages yerine erişim korumalı ücretsiz bir barındırma (ör. Cloudflare Pages + Cloudflare Access) kullanmak.
>
> Şifreler her durumda güvendedir: yalnızca şifreli **GitHub Secrets** içinde tutulur, repoya yazılmaz.

---

## 1. Kurulum (bir kez, ~10 dakika)

### 1.1 Repoyu oluşturun
1. GitHub'da yeni bir repo açın.
2. Bu klasördeki tüm dosyaları repoya yükleyin (`.github` klasörü dahil).

### 1.2 GitHub Secrets tanımlayın
Repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Zorunlu | Açıklama |
|---|---|---|
| `QV_USER` | ✅ | Quietvox kullanıcı adı / e-posta |
| `QV_PASS` | ✅ | Quietvox şifresi |
| `NTFY_TOPIC` | İsteğe bağlı | Bildirim kanalı adı (aşağıya bakın). Tanımlı değilse bildirim gönderilmez. |
| `QV_HTTP_USER` / `QV_HTTP_PASS` | İsteğe bağlı | Yalnızca tarayıcının ilk sorduğu (açılır pencere) kullanıcı/şifre, site içindeki giriş formundakinden **farklıysa** gerekir. |

> Quietvox iki aşamalı giriş ister: önce tarayıcının açılır penceresi (HTTP Basic Auth), ardından
> sayfadaki giriş formu. Scraper ikisine de varsayılan olarak `QV_USER`/`QV_PASS` gönderir.

### 1.3 Actions yazma iznini açın
Repo → **Settings → Actions → General → Workflow permissions → Read and write permissions** → Save.

### 1.4 GitHub Pages'i açın
Repo → **Settings → Pages → Source: Deploy from a branch → Branch: `main` / `(root)`** → Save.
Birkaç dakika sonra adresiniz hazır olur: `https://KULLANICI.github.io/REPO/`

### 1.5 İlk senkronu elle başlatın
Repo → **Actions → Quietvox Senkronizasyon → Run workflow**. Yeşil tik çıktıktan sonra `data.json` gerçek verilerle güncellenir
ve arayüzdeki "Örnek veri gösteriliyor" bandı kaybolur.

---

## 2. Bildirimler (ntfy)

1. Telefona **ntfy** uygulamasını kurun (App Store / Google Play, ücretsiz).
2. Tahmin edilmesi zor bir kanal adı uydurun, ör. `quietvox-baba-8k2m4x` (kanalı bilen herkes okuyabilir).
3. Uygulamada **Subscribe to topic** ile bu ada abone olun.
4. Aynı adı `NTFY_TOPIC` secret'ı olarak kaydedin.

Gelen bildirimler:
- **Her akşam 18:00 (TR):** `📦 Yarın 3 Teslimat (Conrad, Four Seasons) ve 🔄 2 İade Alımı var.`
- **Giriş başarısız olursa:** `⚠️ Quietvox oturumu açılamadı, şifreyi kontrol edin` (sorun sürdükçe tekrar tekrar gönderilmez).

Hatırlatmayı denemek için: **Actions → Run workflow → `send_reminder` kutusunu işaretleyin.**

---

## 3. Telefona kurulum

**iPhone (Safari):** adresi açın → Paylaş düğmesi → **Ana Ekrana Ekle**.

**Android (Chrome):** adresi açın → ⋮ menü → **Uygulamayı yükle / Ana ekrana ekle**.

Artık ana ekrandaki **Quietvox** simgesi tam ekran uygulama gibi açılır.

### Kullanım
- En üstte **Yarın Hatırlatıcısı**: yarın verilecek ve alınacak siparişlerin özeti.
- **📦 Verilecekler / 🔄 Alınacaklar** sekmeleri, **Bugün / Yarın / Tümü** filtreleri.
- Kartta **📞 Ara** ve **WhatsApp** ile rehbere tek dokunuşla ulaşın.
- **Hazırlandı / Teslim Edildi** işaretleri o telefonda kalıcıdır (başka telefonla paylaşılmaz).
- **🖨️ Etiketleri Yazdır (2 Sayfa)**: 1. sayfa çanta üstü WayBill, 2. sayfa çanta içi D&R Formu (QR kodlu).
  Telefonun yazdırma ekranında kâğıt **A4**, ölçek **%100**, "üstbilgi/altbilgi" **kapalı** olmalıdır.

---

## 4. Sorun giderme

| Belirti | Çözüm |
|---|---|
| Kırmızı "oturum açılamadı" bandı | `QV_USER` / `QV_PASS` secret'larını güncelleyin, sonra workflow'u elle çalıştırın. |
| "Veri eski olabilir" uyarısı | **Actions** sekmesinde son çalıştırmanın hatasına bakın. |
| Workflow çalışıyor ama sipariş gelmiyor | Aşağıdaki "Seçicileri ayarlama" bölümüne bakın. |
| Zamanlama gecikiyor | GitHub'ın ücretsiz zamanlayıcısı yoğun saatlerde 5–20 dk gecikebilir; normaldir. |
| Commit hatası (403) | Adım 1.3'teki yazma iznini açın. |

### Seçicileri ayarlama (ilk kurulumda gerekebilir)
Scraper, Quietvox'un iç sayfa yapısı görülmeden, yaygın takvim/modal kalıplarına göre sezgisel olarak yazıldı.
İlk çalıştırmada veriler eksik gelirse bilgisayarda bir kez hata ayıklama dökümü alın:

```bash
pip install -r requirements.txt
```

```bash
python -m playwright install chromium
```

Ardından `QV_USER`, `QV_PASS` ortam değişkenlerini ve `QV_DEBUG=1` ayarlayıp `python scraper.py` çalıştırın.
`debug/` klasörüne takvim ve modal HTML'leri ile ekran görüntüleri yazılır (müşteri verisi içerir, **repoya eklemeyin** —
`.gitignore` zaten engeller). Bu dosyalara bakarak `scraper.py` başındaki seçici ayarları düzeltilir.

### Ortam değişkenleri (ileri düzey)

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `QV_URL_DELIVERY`, `QV_URL_CRUISE_DELIVERY`, `QV_URL_RETURN`, `QV_URL_CRUISE_RETURN` | otomatik keşif | Takvim adreslerini elle belirtir. |
| `SEND_REMINDER` | `0` | `1` ise yarın özeti ntfy'ye gönderilir (workflow 18:00'de kendisi ayarlar). |
| `QV_DEBUG` | `0` | `1` ise `debug/` dökümü alınır. |
| `HEADLESS` | `1` | `0` ise tarayıcı görünür açılır (yalnızca bilgisayarda). |
| `QV_SEL_<ANAHTAR>` | `scraper.py` başındaki `SELECTORS` | Tek bir seçiciyi kod değiştirmeden ezer; adaylar `\|\|` ile ayrılır (ör. `QV_SEL_NEXT_BUTTON`, `QV_SEL_MODAL`, `QV_SEL_EVENT_PRIMARY`). |
| `QV_WINDOW_DAYS` | `10` | Kaç gün ileriye bakılacağı. |
| `QV_PREFER_CLICK` | `0` | `1` ise bağlantılı etkinlikler de tıklanarak açılır. |

Workflow çıkış kodları: `0` başarılı/kısmi, `1` siteye ulaşılamadı, `2` secret eksik, `3` giriş başarısız.

## 5. Testler

Python 3.12 gerekir.

```bash
pip install -r requirements.txt -r tests/requirements-dev.txt
```

```bash
python -m pytest tests -q
```

Testler gerçek Quietvox'a bağlanmaz; `tests/mock_site/` altındaki sahte siteyi kullanır.
