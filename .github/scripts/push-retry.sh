#!/usr/bin/env bash
# Yerelde commit edilmiş değişikliği dala güvenle gönderir: araya giren commit'lerin üzerine
# taşır (rebase; çakışmada bu çalıştırmanın değişikliği kazanır) ve artan beklemeyle
# (5, 10, 20, 40 sn) 5 kez dener. Hata sessizce yutulmaz: 5 denemede gönderilemezse çıkış
# kodu 1 olur ve iş kırmızıya döner.
#
# Neden main'e yazan iş akışları için ortak concurrency grubu kullanılmıyor: GitHub bir grupta
# yalnızca BİR bekleyen çalıştırma tutar ve diğer bekleyenleri sessizce iptal eder. sync,
# version ve action aynı grupta olsaydı sürüm/veri commit'leri kaybolurdu. Çakışmalar burada,
# rebase + yeniden deneme ile kayıpsız çözülür.
#
# Kullanım: bash .github/scripts/push-retry.sh <dal>
set -euo pipefail

branch="${1:?Kullanım: push-retry.sh <dal>}"
delay=5
for attempt in 1 2 3 4 5; do
  if git pull --rebase -X theirs origin "$branch"; then
    if git push origin "HEAD:$branch"; then
      echo "Gönderildi (deneme $attempt)."
      exit 0
    fi
    echo "::warning::Gönderim reddedildi (deneme $attempt)."
  else
    echo "::warning::Rebase başarısız (deneme $attempt); geri alınıyor."
    if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
      git rebase --abort
    fi
  fi
  if [ "$attempt" -lt 5 ]; then
    sleep "$delay"
    delay=$((delay * 2))
  fi
done
echo "::error::5 denemede gönderilemedi."
exit 1
