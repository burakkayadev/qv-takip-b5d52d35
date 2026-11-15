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
- **Her sabah 08:00 ve öğlen 13:00 (TR):** bugün hâlâ bekleyen işler, ör. `📦 Bugün 5 Teslimat (Conrad, …) bekliyor.`
  Quietvox'ta tamamlanmış (Delivered / Returned) siparişler sayılmaz; bekleyen yoksa bildirim gelmez.
- **Her sabah 08:00:** günü geçtiği hâlde alınmamış iadeler, ör. `⚠️ Almayı unuttun: 2 iade hâlâ alınmadı (Conrad).`
  Son 7 gün içinde iade günü geçmiş ve Quietvox'ta hâlâ "Delivered" duran siparişler için gelir.
- **Her akşam 18:00 (TR):** `📦 Yarın 3 Teslimat (Conrad, Four Seasons) ve 🔄 2 İade Alımı var.`
- **Uygulamadan yapılan işlem sonucu:** `✅ Sipariş 2000… Quietvox'ta Delivered yapıldı.` ya da `❌ … yapılamadı: neden`.
- **Giriş başarısız olursa:** `⚠️ Quietvox oturumu açılamadı, şifreyi kontrol edin` (sorun sürdükçe tekrar tekrar gönderilmez).
- **Sipariş değişirse:** önümüzdeki 7 gündeki bir siparişte kişi sayısı, ekipman, tarih ya da otel değişir veya sipariş
  iptal edilirse `🔁 Sipariş değişti — etiketi kontrol et` ve değişikliğin özeti gelir.
- **Senkron bozulursa:** `⚠️ Quietvox senkronu başarısız oldu` (bir kez; sorun sürdükçe tekrar gönderilmez).
- Bildirime dokununca uygulama doğrudan ilgili listede açılır.

Hatırlatmayı denemek için: **Actions → Quietvox Senkronizasyon → Run workflow → `reminder` alanından tür seçin.**

---

## 3. Telefona kurulum

**iPhone (Safari):** adresi açın → Paylaş düğmesi → **Ana Ekrana Ekle**.

**Android (Chrome):** adresi açın → ⋮ menü → **Uygulamayı yükle / Ana ekrana ekle**.

Artık ana ekrandaki **Quietvox** simgesi tam ekran uygulama gibi açılır.

### Android uygulaması (APK)
Telefonda şu adresi açıp dosyayı indirin ve kurun (ilk kurulumda "bilinmeyen kaynaklara izin ver" sorusu çıkar):

`https://github.com/burakkayadev/qv-takip-b5d52d35/releases/download/apk/quietvox-takip.apk`

- Uygulama, web sürümünü Chrome motoruyla tam ekran açan ince bir kabuktur; web sürümü güncellenince
  uygulama da kendiliğinden güncellenir, APK'yı yeniden kurmak gerekmez.
- Telefonda Chrome yüklü ve güncel olmalıdır.
- Yeniden derlemek için: **Actions → Android APK → Run workflow**. İmza anahtarı `ANDROID_KEYSTORE_B64` /
  `ANDROID_KEYSTORE_PASS` secret'larındadır; yedeği bilgisayardaki `android-signing/` klasöründedir (repoya girmez).
- Adres çubuğunun gizlenmesi için `burakkayadev.github.io` reposundaki `.well-known/assetlinks.json` dosyası gerekir; silmeyin.

### Kullanım
- **☰ Yan menü:** Verilecekler, Alınacaklar, Geciken iadeler; gün (Bugün / Yarın / Tümü); koyu mod;
  **yazı boyutu** (normal, büyük, çok büyük); **aylık rapor**; Quietvox bağlantısı.
- **Bugün paneli:** kaç iş var, kaçı tamam; yarının tek satır özeti; alınmamış iade varsa kırmızı **Geciken iade**.
- **Tarih çubuğu:** `‹` `›` ile gün gün gezin ya da tarihe dokunup gün seçin (son 62 gün, önümüzdeki 31 gün).
- **🔍 Arama:** otel, QV ID, müşteri, rehber adı ya da telefonu yazın; tüm tarihlerde aranır.
- **Otel filtresi:** otel adına dokununca yalnızca o otelin siparişleri görünür.
- **📊 Özet:** görünümdeki siparişlerin ekipman toplamı (toplam RX, TX, pil…) ve otel bazında sipariş/kişi/RX;
  tek sayfa A4 olarak yazdırılabilir.
- **🗺️ Rota:** görünümdeki otelleri (en fazla 10) Google Haritalar'da tek rota olarak açar.
- **☑ Seç:** birden çok siparişi seçip tek seferde **Quietvox'a gönderin** ya da WayBill'lerini **bir A4'e ikişer** yazdırın.
- **Kart:** durum rozeti, ⚠️ eksik veri uyarıları (ekipman boş, RX kişi sayısından az, rehber telefonu yok),
  🔁 **Değişti** rozeti (etiket basıldıktan sonra Quietvox'ta kişi/ekipman/tarih değiştiyse; ayrıntısı "Ayrıntılar"da).
- **Ara / WhatsApp / Harita / Yazdır:** WhatsApp, rehberin anlatım diline göre (Almanca, İngilizce, Fransızca,
  İspanyolca, İtalyanca, Türkçe) hazır bir mesajla açılır; göndermeden önce düzenleyebilirsiniz.
- **Yazdır:** WayBill (yarım A4, yatay) ya da D&R Formu (A4); her ikisinde Önizle var. Yazdırma ekranında kâğıt **A4**,
  ölçek **%100**, "üstbilgi/altbilgi" **kapalı** olmalıdır. WayBill'i kesikli çizgiden kesin.
- **Kısayollar:** uygulama simgesine uzun basınca "Bugün verilecekler / alınacaklar / Geciken iadeler".
- **Aylık rapor:** seçilen ayın teslimat ve iadeleri ile otel bazında özet; Excel'de açılan dosya (CSV) olarak iner.

### Quietvox'a yazma (Hazırlandı / Teslim Alındı)
- **Hazırlandı** (Verilecekler): Quietvox'ta durumu **Delivered** yapar, **Self Delivery** ve **Self Return** işaretler.
- **Teslim Alındı** (Alınacaklar): durumu **Returned** yapar, **Self Return** işaretler ve iade adetlerini doldurur:
  Battery AA ve Earphone 3.5mm New Style için teslim adedinin %3'ü (yukarı yuvarlanır), diğer tüm kalemler teslim adediyle aynı.
- Butona basınca önce ne yazılacağını gösteren bir onay penceresi açılır. Onaydan sonra işlem 2–3 dakikada
  tamamlanır; kartta "⏳ Quietvox'a gönderildi" görünür, sonuç ntfy bildirimi olarak da gelir.
- Quietvox beklenmedik bir soru sorarsa ya da hata verirse işlem **kaydedilmez** ve nedenini bildirir.

Bunun için telefonda **bir kez** erişim anahtarı girilir:
1. GitHub → sağ üst profil → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. **Repository access:** *Only select repositories* → bu repo. **Permissions → Repository permissions → Actions: Read and write**.
3. Oluşan `github_pat_…` anahtarını kopyalayın; uygulamada **⚙ → anahtarı yapıştırın → Kaydet ve dene**.

Anahtar yalnızca o telefonda saklanır ve yalnızca bu repodaki iş akışlarını başlatabilir. Anahtar girilmezse
butonlar yalnızca uygulama içinde işaret koyar ("Yalnızca burada işaretle").

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
Scraper siparişleri takvime tıklamadan, Quietvox'un kendi veri adreslerinden okur:
etkinlik listesi `…/calendar/getContent.html`, belgeler `/order/wayBill` ve `/order/deliveryReturnFormView`.
Bu yapı canlı sitede doğrulandı. Quietvox sayfalarını değiştirirse ve veriler eksik gelmeye başlarsa
bilgisayarda bir kez hata ayıklama dökümü alın:

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
| `QV_CLICK_FALLBACK` | gerçek sitede `0` | `1` ise veri adresleri okunamadığında takvime tıklayan eski yola düşülür. Gerçek sitede açmayın: boş güne tıklamak "Create Order" penceresini açar. |

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
