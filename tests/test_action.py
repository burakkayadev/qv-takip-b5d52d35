"""qv_action.py içindeki saf kuralların testleri (Quietvox'a bağlanmaz)."""
import pytest

import qv_action


@pytest.mark.parametrize("model, delivered, expected", [
    ("QV9.2 : QV9 TX EU", "1", "1"),
    ("QV9.1 : QV9 RX EU", "16", "16"),
    ("QV1.14 : Carrying case small", "1", "1"),
    ("QV1.300 : Earphone over the Ear/hearing aid 3.5mm Jack", "3", "3"),
    ("QV1.38 : Battery AA disposable", "6", "1"),
    ("QV1.38 : Battery AA disposable", "68", "3"),
    ("QV1.38 : Battery AA disposable", "100", "3"),
    ("QV1.2S : Earphone 3.5mm New Style 2017RP", "24", "1"),
    ("QV1.2S : Earphone 3.5mm New Style 2017RP", "0", "0"),
])
def test_return_count_rule(model, delivered, expected):
    assert qv_action.planned_return_count(model, delivered) == expected


def test_non_numeric_delivery_count_is_left_alone():
    assert qv_action.planned_return_count("Lanyards", "VIKING Lanyards") is None
    assert qv_action.planned_return_count("QV9.2 : QV9 TX EU", "") is None


@pytest.mark.parametrize("env, problem", [
    ({"QV_ACTION": "sil", "QV_ORDER_ID": "2000000001", "QV_ORDER_DATE": "2026-11-14"}, "QV_ACTION"),
    ({"QV_ACTION": "returned", "QV_ORDER_ID": "12 34x", "QV_ORDER_DATE": "2026-11-14"}, "QV_ORDER_ID"),
    ({"QV_ACTION": "returned", "QV_ORDER_ID": "2000000001", "QV_ORDER_DATE": "14.11.2026"}, "QV_ORDER_DATE"),
])
def test_invalid_requests_are_rejected(env, problem):
    assert problem in qv_action.read_request(env)


def test_status_codes_match_quietvox_form_values():
    # Canlı sitede doğrulandı: 2 = Delivered, 6 = Returned (5 Confirmed'dır, kullanılmamalı)
    import scraper

    assert qv_action.ACTIONS["delivered"].status_value == "2"
    assert qv_action.ACTIONS["returned"].status_value == "6"
    assert scraper.ORDER_STATUS_NAMES[2] == "delivered"
    assert scraper.ORDER_STATUS_NAMES[5] == "confirmed"


def test_valid_request_selects_calendar_and_status():
    spec, order_id, day = qv_action.read_request(
        {"QV_ACTION": "Returned", "QV_ORDER_ID": "2000000001", "QV_ORDER_DATE": "2026-11-14"})

    assert (spec.status_value, spec.kind, order_id, day.isoformat()) == ("6", "return", "2000000001", "2026-11-14")


def test_read_requests_supports_bulk_order_ids():
    spec, orders = qv_action.read_requests({
        "QV_ACTION": "delivered",
        "QV_ORDER_ID": "2000221883",
        "QV_ORDER_DATE": "2026-11-19",
        "QV_ORDER_IDS": "2000221883:2026-11-19, 2000264994:2026-11-19, 2000221883:2026-11-19, 2000265002",
    })
    assert spec.status_value == "2"
    assert [(oid, d.isoformat()) for oid, d in orders] == [
        ("2000221883", "2026-11-19"),
        ("2000264994", "2026-11-19"),
        ("2000265002", "2026-11-19"),
    ]


def test_js_form_problems_auto_clicks_no_sms_confirmation():
    assert "no,\\s*just\\s*save" in qv_action.JS_FORM_PROBLEMS
    assert "qvConfirmed" in qv_action.JS_FORM_PROBLEMS

