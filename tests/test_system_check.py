"""5 kapılı sistem denetiminin saf kurallarının testleri (ağa, portala, tarayıcıya bağlanmaz)."""
from datetime import datetime, timedelta, timezone

import system_check
import ui_gates

NOW = datetime(2026, 11, 19, 18, 0, tzinfo=timezone.utc)


def _run(minutes_ago, conclusion, title="", status="completed"):
    created = (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")
    return {"createdAt": created, "conclusion": conclusion, "status": status, "displayTitle": title, "url": "u"}


# --- Kapı 1 -------------------------------------------------------------------------------------

def test_freshness_gate_is_red_after_90_minutes_or_when_status_is_not_ok():
    def doc(minutes, status="ok"):
        stamp = (NOW - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
        return {"generated_at": stamp, "status": status}

    assert system_check.gate_freshness(doc(30), now=NOW)["ok"] is True
    assert system_check.gate_freshness(doc(91), now=NOW)["ok"] is False
    assert system_check.gate_freshness(doc(10, "login_failed"), now=NOW)["ok"] is False
    assert system_check.gate_freshness({"generated_at": "bozuk"}, now=NOW)["ok"] is False
    assert system_check.gate_freshness(None, "HTTP 404", now=NOW)["ok"] is False


# --- Kapı 2 -------------------------------------------------------------------------------------

def test_sync_failure_is_compensated_only_by_a_later_success():
    runs = [_run(300, "success"), _run(200, "failure"), _run(100, "success")]
    assert system_check.uncompensated(runs, NOW, system_check.sync_covered) == []
    runs = [_run(300, "success"), _run(100, "failure")]  # en son çalıştırma başarısız
    assert len(system_check.uncompensated(runs, NOW, system_check.sync_covered)) == 1


def test_old_cancelled_and_running_runs_are_ignored():
    runs = [_run(60 * 25, "failure"), _run(50, "cancelled"), _run(5, "", status="in_progress")]
    assert system_check.uncompensated(runs, NOW, system_check.sync_covered) == []


def test_action_failure_needs_a_later_success_for_the_same_orders_and_action():
    failed = _run(100, "failure", "returned · 2000221686:2026-11-14 · 2026-11-14")
    same = _run(50, "success", "returned · 2000221582:2026-11-14,2000221686:2026-11-14 · 2026-11-14")
    other = _run(50, "success", "returned · 2000221999:2026-11-14 · 2026-11-14")
    other_kind = _run(50, "success", "delivered · 2000221686:2026-11-14 · 2026-11-14")
    assert system_check.uncompensated([failed, same], NOW, system_check.action_covered) == []
    assert len(system_check.uncompensated([failed, other], NOW, system_check.action_covered)) == 1
    assert len(system_check.uncompensated([failed, other_kind], NOW, system_check.action_covered)) == 1


def test_workflow_gate_reports_uncompensated_failures_and_read_errors():
    def fetch(workflow, limit):
        return [_run(30, "failure", "x")] if workflow == "sync.yml" else []

    def broken(workflow, limit):
        raise RuntimeError("gh yok")

    assert system_check.gate_workflow_runs(NOW, fetch)["ok"] is False
    assert system_check.gate_workflow_runs(NOW, lambda w, n: [_run(30, "success")])["ok"] is True
    assert system_check.gate_workflow_runs(NOW, broken)["ok"] is False


# --- Kapı 3 -------------------------------------------------------------------------------------

def test_targets_are_required_only_while_they_are_in_data_json():
    document = {"deliveries": [{"id": "2000221582"}], "returns": [{"id": "2000221686_2026-11-15"}]}
    assert system_check.targets_in(document) == ["2000221582", "2000221686"]
    assert system_check.targets_in({"deliveries": [], "returns": []}) == []


# --- Kapı 4-5 (ui_gates saf kuralları) --------------------------------------------------------

def _order(oid, day, status, hotel="Otel A", client="Acente A"):
    return {"id": oid, "date": day, "status": status, "location_name": hotel, "waybill": {"client": client}}


def test_expected_counts_match_index_html_rules():
    document = {
        "deliveries": [_order("1", "2026-11-20", "confirmed"), _order("2", "2026-11-19", "delivered"),
                       _order("3", "2026-11-25", "cancelled"), _order("4", "2026-11-10", "confirmed")],
        "returns": [_order("2", "2026-11-21", "delivered"), _order("5", "2026-11-12", "delivered"),
                    _order("6", "2026-11-13", "returned"), _order("7", "2026-11-22", "returned")],
    }
    counts = ui_gates.expected_counts(document, "2026-11-19")
    # verilecek: 1 (bugünden sonra, bekleyen); alınacak: 2 (bugünden sonra, Returned değil)
    # arşiv: 6, 7 (Returned) + 2 (Delivered teslimat) = 3; geciken: 5 (geçmiş, hâlâ Delivered)
    assert counts == {"deliveries": 1, "returns": 1, "archive": 3, "overdue": 1}


def test_expected_filters_count_distinct_hotels_and_agencies():
    document = {"deliveries": [_order("1", "d", "s", "Otel A", "X"), _order("2", "d", "s", "Otel B", "X")],
                "returns": [{"id": "3", "title": "Otel C", "dr_form": {"customer": "Y"}}]}
    assert ui_gates.expected_filters(document) == (3, 2)


def test_green_badge_only_on_real_status_and_only_with_fresh_data():
    cards = [{"id": "1", "status": "returned", "kind": "return", "verified": True},
             {"id": "2", "status": "delivered", "kind": "return", "verified": True},
             {"id": "3", "status": "delivered", "kind": "delivery", "verified": False}]
    fresh = ui_gates.badge_problems(cards, stale=False, view="archive")
    assert any("'delivered' iken" in p for p in fresh)  # kart 2: Delivered ama "Doğrulandı"
    assert any("rozeti yok" in p for p in fresh)  # kart 3: taze ve Delivered ama rozet eksik
    stale = ui_gates.badge_problems(cards[:1], stale=True, view="archive")
    assert stale and "eskiyken" in stale[0]


def test_data_age_minutes_handles_missing_timezone_and_garbage():
    assert round(ui_gates.data_age_minutes({"generated_at": "2026-11-19T17:30:00"}, NOW)) == 30
    assert ui_gates.data_age_minutes({"generated_at": ""}, NOW) is None
