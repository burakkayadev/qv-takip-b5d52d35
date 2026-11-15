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
| `QV_PASS_BACKUP` | İsteğe bağlı | İkinci (yedek) şifre: `QV_PASS` reddedilirse otomatik denenir. Şifre değişeceği önceden biliniyorsa yenisini buraya girin. |
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
2. **Repository access:** *Only select repositories* → bu repo. **Permissions → Repository permissions → Actions: Read and write**
   ve (şifreyi uygulamadan güncelleyebilmek için) **Secrets: Read and write**. Mevcut anahtara bu izni sonradan
   eklemek için anahtarı açıp **Edit** deyin; anahtar değişmez, telefona yeniden girmek gerekmez.
3. Oluşan `github_pat_…` anahtarını kopyalayın; uygulamada **⚙ → anahtarı yapıştırın → Kaydet ve dene**.

Anahtar yalnızca o telefonda saklanır ve yalnızca bu repodaki iş akışlarını başlatabilir. Anahtar girilmezse
butonlar yalnızca uygulama içinde işaret koyar ("Yalnızca burada işaretle").

### Yazdırma seçenekleri ve otomatik Delivered
Kartın **🖨️ Yazdır** düğmesi ve toplu seçimde **🖨️ Toplu Yazdır (3 seçenek)** aynı pencereyi açar:

| Seçenek | Ne basılır | Otomatik işlem |
|---|---|---|
| 📄 + 🏷️ **Form + WayBill** | Önce tüm Formlar, sonra tüm WayBill'ler (her biri ayrı sayfa) | Evet |
| 🏷️ **Sadece WayBill** | Yalnızca WayBill'ler, A4'e ikişer | Hayır |
| 📄 **Sadece Form** | Yalnızca Delivery & Return Formları | Evet |

"Form" içeren bir baskıda **Yazdır**'a basıldığı anda:
1. Kartlara kalıcı **🖨️ Form Yazdırıldı ✓** rozeti eklenir (o telefonda saklanır).
2. Siparişler Quietvox'a **Delivered** olarak gönderilir (Quietvox İşlem iş akışı; internet yoksa kuyruğa alınır,
   bağlantı gelince gönderilir). Zaten Delivered olan ya da gönderimi süren sipariş tekrar gönderilmez.
3. Tek bir **bilgilendirme e-postası** gider (aşağıdaki kurulum gerekir).

Not: işlem **Yazdır**'a basınca başlar; telefonun yazdırma ekranında iptal edilse de geri alınmaz.

### E-posta bildirimi (kurulum, bir kez)
E-posta GitHub Actions'taki **Quietvox Bildirim** iş akışından (`notify.yml` → `notify_mail.py`) Gmail ile gönderilir.
Konu: `Quietvox: X Sipariş Formu Basıldı ve Teslim Edildi (Delivered)`; gövdede QV ID, otel, acente, kişi/ekipman,
teslim/iade tarihi ve işlem saati tablosu. Alıcı adresleri repoya yazılmaz, Secret olarak tutulur.

1. Gönderen Gmail hesabında **2 Adımlı Doğrulama**'yı açın, sonra **Google Hesabı → Güvenlik → Uygulama şifreleri**'nden
   16 haneli bir uygulama şifresi oluşturun.
2. Secret'ları girin (her komut değeri sorar):

```bash
gh secret set SMTP_USER -R burakkayadev/qv-takip-b5d52d35
```

```bash
gh secret set SMTP_PASS -R burakkayadev/qv-takip-b5d52d35
```

```bash
gh secret set MAIL_TO -R burakkayadev/qv-takip-b5d52d35
```

`SMTP_USER`: gönderen Gmail adresi · `SMTP_PASS`: uygulama şifresi · `MAIL_TO`: alıcılar, virgülle (ör. `a@x.com,b@y.com`).
Gmail dışı sağlayıcı için isteğe bağlı `SMTP_HOST` ve `SMTP_PORT` (varsayılan `smtp.gmail.com`, `465`).
Secret'lar girilmezse iş akışı hata vermeden biter, e-posta gönderilmez.

### Quietvox şifresi değişirse (giriş koruması)
Quietvox girişi başarısız olursa (yanlış şifre, oturum reddi, giriş sayfasına geri dönme):
- `data.json`'daki siparişler **silinmez**; yalnızca durum `login_failed` olur. Uygulama son verilerle çalışmaya devam eder.
- Uygulamanın üstünde kırmızı **⚠️ Quietvox oturumu güncellenmeli — Yeni şifreyi girin** bandı çıkar.
- `MAIL_TO` (ya da tanımlıysa `ALERT_TO`) alıcılarına **⚠️ ACİL: Quietvox Şifresi Değişmiş Olabilir - Giriş Başarısız**
  e-postası gider. Sorun sürerse en fazla **saatte bir** tekrarlanır (son gönderim `data.json` → `login_alert_at`).
- `QV_PASS_BACKUP` tanımlıysa önce o denenir; yedek şifreyle girilirse veriler normal güncellenir ve sarı
  **🔑 yedek şifreyle devam ediliyor** bandı çıkar.

**Quietvox şifresini uygulamadan güncelleme (10 saniye):** bandına dokunun (ya da ☰ → ⚙ Quietvox bağlantısı) →
**🔑 Quietvox Şifresini Güncelle** → yeni şifre → **Kaydet ve Dene**. Şifre telefonda GitHub'ın açık anahtarıyla
şifrelenip `QV_PASS` Secret'ına yazılır (düz metin hiçbir yere kaydedilmez), ardından senkron çalıştırılıp yeni şifreyle
giriş denenir ve sonuç ("✅ Yeni şifre çalışıyor" / "❌ Giriş yine başarısız") 2-4 dakikada aynı kutuda görünür.
**Yedek şifre olarak kaydet** düğmesi aynı şekilde `QV_PASS_BACKUP`'a yazar. Bunun için GitHub anahtarında
**Secrets: Read and write** izni gerekir (bkz. "Quietvox'a yazma"). Bilgisayardan: repo → Settings → Secrets and
variables → Actions → `QV_PASS` → Update.

### Yedekleme (Google Drive + flash bellek)
**Haftalık otomatik yedek:** `.github/workflows/backup.yml` ("Quietvox Yedek") her Pazar 03:30'da repoyu ZIP'ler;
ZIP Actions sayfasında 90 gün saklanır ve `RCLONE_CONFIG` Secret'ı tanımlıysa Google Drive'daki `QuietVox_Yedekler`
klasörüne yüklenir (120 günden eskiler silinir; klasör adı `DRIVE_BACKUP_FOLDER` Secret'ıyla değiştirilebilir).
`.gitignore`'daki dosyalar (Android imza anahtarı, çerezler, yerel yedekler) bu yedeğe girmez.

Drive bağlantısı (bir kez, bilgisayarda):

```bash
winget install Rclone.Rclone
```

```bash
rclone config
```

`n` (yeni) → ad: `gdrive` → tür: `drive` → client_id/secret boş → scope: `drive.file` → tarayıcıda Google hesabına izin →
kalan sorular varsayılan. Sonra yapılandırmayı Secret olarak girin:

```bash
gh secret set RCLONE_CONFIG -R burakkayadev/qv-takip-b5d52d35 < "$APPDATA/rclone/rclone.conf"
```

Deneme için Actions → **Quietvox Yedek (Google Drive)** → **Run workflow**.

**Flash bellek yedeği:** masaüstündeki `YEDEK_FLASH_BELLEK/QuietVox_Tam_Yedek_<tarih>` klasörü ve aynı adlı `.zip`
doğrudan USB belleğe kopyalanır. İçindeki `BENI_OKU_KURULUM.txt` repo adresini, Secret listesini ve bilgisayar sıfırlanırsa
sistemi baştan kurma adımlarını anlatır. Bu klasör Android imza anahtarını da içerdiği için güvenli yerde saklayın.

---

## 4. Sorun giderme

| Belirti | Çözüm |
|---|---|
| Kırmızı "Quietvox oturumu güncellenmeli" bandı | Banda dokunup **🔑 Quietvox Şifresini Güncelle → Kaydet ve Dene** (bkz. "Quietvox şifresi değişirse"). |
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
