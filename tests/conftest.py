"""Ortak pytest ayarları: proje kökünü içe aktarma yoluna ekler."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for entry in (str(ROOT), str(Path(__file__).resolve().parent)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import scraper  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> scraper.Settings:
    """Geçici dizine yazan, sabit 'bugün'lü test ayarları."""
    return scraper.load_settings({
        "QV_USER": "test-kullanici", "QV_PASS": "test-sifre-123",
        "QV_OUTPUT": str(tmp_path / "data.json"), "QV_COOKIES": str(tmp_path / "cookies.json"),
        "QV_TODAY": "2026-11-25",
    })


@pytest.fixture
def today() -> date:
    return date(2026, 11, 25)
