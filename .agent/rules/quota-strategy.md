# Stratejik Limit & Model Yönlendirme Kuralı

- **Model Dağılımı:**
  - Standart UI, etiket, CSS ve önizleme işleri **Antigravity (`Gemini 4 Hydrogen Medium/High`)** üzerinden yürütülür.
  - **Claude CLI** kotası yalnızca kritik mimari ve karmaşık hata ayıklama için korunur.
- **Önizleme & Yayın Akışı:**
  - Arayüz değişikliklerinde önce yerel önizleme (`http://localhost:8080/...`) sunulur, kullanıcı onayından sonra tek seferde `git push` atılır.
