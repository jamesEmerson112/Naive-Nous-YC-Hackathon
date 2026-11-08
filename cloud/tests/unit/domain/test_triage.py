"""domain/triage.py: work-order decision, spend caps, card text, card note parsing."""

import ast
import re
import uuid
from pathlib import Path

import pytest
import yaml

from romulus_cloud.config import PARTS_PATH, load_parts
from romulus_cloud.domain import triage
from romulus_cloud.domain.triage import (
    Caps,
    CatalogError,
    Part,
    PartsCatalog,
    card_outcome,
    check_caps,
    needs_work_order,
    parse_card_note,
    parse_catalog,
    render_card,
)
from romulus_contracts import IncidentReport

SERVO = Part(
    id="sts3215-servo",
    name="Feetech STS3215 servo (12 V)",
    fault_kinds=("gripper_stall",),
    supplier="Test desk",
    phone="TEAMMATE_PHONE",
    typical_usd=18,
    max_usd=40,
)
CATALOG = PartsCatalog(parts=(SERVO,))
CAPS = Caps(order_max_usd=50, orders_per_day_max=3, orders_per_robot_day_max=1)


def incident(kind="gripper_stall", outcome=None, summary="Gripper current above 900 mA"):
    body = {
        "incident_id": str(uuid.uuid4()),
        "occurred_at": "2026-11-07T22:00:00Z",
        "kind": kind,
        "summary": summary,
    }
    if outcome is not None:
        body["outcome"] = outcome
    return IncidentReport.model_validate(body)


# --- needs_work_order ---------------------------------------------------------------------


def test_unresolved_hardware_gets_a_work_order():
    inc = incident(outcome={"resolved": False, "suspected_cause": "hardware"})
    spec = needs_work_order(inc, CATALOG, device_id="arm-1")
    assert spec is not None
    assert spec.part == SERVO
    assert spec.device_id == "arm-1"
    assert spec.incident_id == str(inc.incident_id)
    assert spec.max_usd == 40 and spec.est_cost_usd == 18 and spec.quantity == 1
    assert "hardware cause" in spec.why


def test_park_category_means_hardware_even_if_cause_says_otherwise():
    inc = incident(outcome={"resolved": False, "suspected_cause": "software", "category": "park"})
    spec = needs_work_order(inc, CATALOG, device_id="arm-1")
    assert spec is not None and "parked" in spec.why


def test_cause_and_category_are_case_insensitive():
    inc = incident(outcome={"suspected_cause": " Hardware "})
    assert needs_work_order(inc, CATALOG, device_id="arm-1") is not None
    inc = incident(outcome={"category": "PARK"})
    assert needs_work_order(inc, CATALOG, device_id="arm-1") is not None


@pytest.mark.parametrize(
    "outcome",
    [
        None,  # missing outcome: unresolved, cause unknown
        {"resolved": True, "suspected_cause": "hardware"},
        {"resolved": True, "category": "park"},
        {"resolved": False, "suspected_cause": "software"},
        {"resolved": False, "suspected_cause": "unknown"},
        {"resolved": False, "suspected_cause": "gremlins", "category": "fix-code"},
    ],
)
def test_no_work_order(outcome):
    assert needs_work_order(incident(outcome=outcome), CATALOG, device_id="arm-1") is None


def test_unknown_kind_gives_spec_without_part_and_needs_human():
    inc = incident(kind="flux_capacitor", outcome={"suspected_cause": "hardware"})
    spec = needs_work_order(inc, CATALOG, device_id="arm-1")
    assert spec is not None and spec.part is None
    assert "no part" in spec.why
    decision = check_caps(spec, 0, 0, CAPS)
    assert decision.status == "needs_human" and not decision.ok
    with pytest.raises(ValueError):
        render_card(spec, "wo_1", deliver_to="bench")


def test_summary_is_flattened_and_truncated():
    inc = incident(
        outcome={"suspected_cause": "hardware"}, summary="line one\nline two\t" + "x" * 1000
    )
    spec = needs_work_order(inc, CATALOG, device_id="arm-1")
    assert "\n" not in spec.summary
    assert len(spec.summary) <= triage.SUMMARY_MAX and spec.summary.endswith("…")


# --- check_caps ---------------------------------------------------------------------------


def hw_spec(max_usd=40.0):
    spec = needs_work_order(
        incident(outcome={"suspected_cause": "hardware"}), CATALOG, device_id="arm-1"
    )
    return triage.WorkOrderSpec(**{**spec.__dict__, "max_usd": max_usd})


def test_caps_ok():
    d = check_caps(hw_spec(), orders_today=0, orders_for_robot_today=0, caps=CAPS)
    assert d.ok and d.status == "ok" and "Within caps" in d.why


def test_price_exactly_at_cap_passes_and_one_cent_over_blocks():
    assert check_caps(hw_spec(50.0), 0, 0, CAPS).ok
    d = check_caps(hw_spec(50.01), 0, 0, CAPS)
    assert d.status == "blocked_over_cap" and "ORDER_MAX_USD" in d.why


def test_daily_count_at_cap_blocks_and_one_below_passes():
    assert check_caps(hw_spec(), 2, 0, CAPS).ok
    d = check_caps(hw_spec(), 3, 0, CAPS)
    assert d.status == "blocked_over_cap" and "ORDERS_PER_DAY_MAX" in d.why


def test_robot_count_at_cap_blocks():
    d = check_caps(hw_spec(), 0, 1, CAPS)
    assert d.status == "blocked_over_cap" and "ORDERS_PER_ROBOT_DAY_MAX" in d.why
    assert "arm-1" in d.why


def test_every_broken_cap_is_named():
    d = check_caps(hw_spec(99), 3, 1, CAPS)
    assert all(k in d.why for k in ("ORDER_MAX_USD", "ORDERS_PER_DAY_MAX", "ORDERS_PER_ROBOT"))


def test_zero_caps_block_everything():
    d = check_caps(hw_spec(), 0, 0, Caps(50, 0, 0))
    assert d.status == "blocked_over_cap"


# --- render_card --------------------------------------------------------------------------


def test_card_matches_template():
    card = render_card(hw_spec(), "wo_17", deliver_to="YC, 1 Main St", robot="SO-101, bench 1")
    assert card.title == "wo_17 · arm-1 · replace Feetech STS3215 servo (12 V)"
    lines = card.body.splitlines()
    assert lines[0] == "WORK ORDER wo_17"
    assert "Robot: arm-1 (SO-101, bench 1)" in lines
    assert "Request: order 1 × Feetech STS3215 servo (12 V)" in lines
    assert "Supplier: Test desk · TEAMMATE_PHONE" in lines
    assert "MAX_USD: 40" in lines
    assert "Deliver to: YC, 1 Main St" in lines
    assert "  RESULT: ordered ORDER_REF=<ref> COST_USD=<amount>" in lines
    assert lines[2].startswith("Fault: gripper_stall. Gripper current")
    assert len(card.body) <= 20_000


def test_long_title_is_capped():
    long = Part(**{**SERVO.__dict__, "name": "x" * 300})
    spec = triage.WorkOrderSpec(**{**hw_spec().__dict__, "part": long})
    assert len(render_card(spec, "wo_1", deliver_to="bench").title) == triage.TITLE_MAX


# --- parse_card_note / card_outcome -------------------------------------------------------


def test_result_note():
    o = parse_card_note("RESULT: ordered ORDER_REF=PO-991 COST_USD=38.50\nthanks")
    assert (o.status, o.order_ref, o.cost_usd) == ("done", "PO-991", 38.5)
    assert o.why == "Ordered: ref PO-991, $38.50"


@pytest.mark.parametrize(
    "note",
    [
        "\n\n  result: ordered cost_usd=$12 order_ref=A1  ",
        "RESULT: ordered ORDER_REF=A1 COST_USD=12.00 extra=1",
    ],
)
def test_result_note_lenient_forms(note):
    o = parse_card_note(note)
    assert o.status == "done" and o.order_ref == "A1" and o.cost_usd == 12.0


def test_stopped_note():
    o = parse_card_note("STOPPED: part out of stock")
    assert (o.status, o.reason, o.why) == (
        "stopped",
        "part out of stock",
        "Stopped: part out of stock",
    )
    assert parse_card_note("STOPPED:").reason == "no reason given"


@pytest.mark.parametrize(
    "note",
    [
        None,
        "",
        "   \n  ",
        "Done! Ordered it.",
        "RESULT: ordered COST_USD=12",  # no ref
        "RESULT: ordered ORDER_REF=A1",  # no cost
        "RESULT: ordered ORDER_REF=A1 COST_USD=twelve",
        "RESULT: ordered ORDER_REF=A1 COST_USD=-5",
        "RESULT: test",  # the M0 spike note
        "some preface\nRESULT: ordered ORDER_REF=A1 COST_USD=1",  # not the first line
    ],
)
def test_malformed_notes_need_a_human(note):
    o = parse_card_note(note)
    assert o.status == "needs_human" and o.order_ref is None and o.why


@pytest.mark.parametrize(
    ("status", "note", "expected"),
    [
        ("todo", None, "in_progress"),
        ("doing", None, "in_progress"),
        ("done", "RESULT: ordered ORDER_REF=X COST_USD=1", "done"),
        ("done", "STOPPED: too pricey", "stopped"),
        ("done", None, "needs_human"),
        ("blocked", None, "needs_human"),
        ("archived", None, "needs_human"),
    ],
)
def test_card_outcome(status, note, expected):
    assert card_outcome(status, note).status == expected


def test_blocked_card_quotes_last_comment():
    o = card_outcome("blocked", None, last_comment="Price is $55, over MAX_USD")
    assert o.why == "Parked by Naïve: Price is $55, over MAX_USD"


def test_fake_naive_notes_parse():
    """The FakeNaive adapter's notes must parse as done/stopped (keeps the two in sync)."""
    o = parse_card_note("RESULT: ordered ORDER_REF=FAKE-0001 COST_USD=12.00\n(fake Naïve: …)")
    assert o.status == "done"


# --- catalogue ----------------------------------------------------------------------------


def test_repo_parts_yaml_loads_and_uses_only_placeholder_phones():
    catalog = load_parts()
    assert catalog.for_kind("gripper_stall").id == "sts3215-servo"
    assert catalog.for_kind("GRIPPER_STALL") is not None
    assert catalog.for_kind("no_such_kind") is None
    ids = [p.id for p in catalog.parts]
    assert {"sts3215-servo", "usb-camera", "hc-sr04"} <= set(ids)
    for part in catalog.parts:  # K13: never a real business's number
        assert part.phone == "TEAMMATE_PHONE", part.id
        assert "placeholder" in part.supplier.lower(), part.id
        assert not re.search(r"\d{3}.?\d{3}.?\d{4}", part.phone)
        assert part.typical_usd <= part.max_usd <= CAPS.order_max_usd
    assert "K13" in PARTS_PATH.read_text()


def test_parse_catalog_defaults_and_overrides():
    catalog = parse_catalog(
        {
            "defaults": {"supplier": "Desk", "phone": "P"},
            "parts": [
                {"id": "a", "name": "A", "fault_kinds": ["X"], "typical_usd": 1, "max_usd": 2},
                {
                    "id": "b",
                    "name": "B",
                    "fault_kinds": ["y"],
                    "typical_usd": 1,
                    "max_usd": 2,
                    "phone": "Q",
                    "quantity": 2,
                },
            ],
        }
    )
    a, b = catalog.parts
    assert (a.supplier, a.phone, a.fault_kinds, a.quantity) == ("Desk", "P", ("x",), 1)
    assert (b.phone, b.quantity) == ("Q", 2)


def test_quantity_scales_estimate():
    part = Part(**{**SERVO.__dict__, "quantity": 2})
    spec = needs_work_order(
        incident(outcome={"suspected_cause": "hardware"}), PartsCatalog((part,)), device_id="arm-2"
    )
    assert spec.quantity == 2 and spec.est_cost_usd == 36


@pytest.mark.parametrize("data", [None, {}, {"parts": None}])
def test_empty_catalog(data):
    assert parse_catalog(data).parts == ()


GOOD = {
    "id": "a",
    "name": "A",
    "fault_kinds": ["x"],
    "typical_usd": 1,
    "max_usd": 2,
    "supplier": "S",
    "phone": "P",
}


@pytest.mark.parametrize(
    "bad",
    [
        ["not a mapping"],
        [{k: v for k, v in GOOD.items() if k != "max_usd"}],
        [{**GOOD, "fault_kinds": "x"}],
        [{**GOOD, "fault_kinds": []}],
        [{**GOOD, "max_usd": -1}],
        [{**GOOD, "typical_usd": True}],
        [{**GOOD, "quantity": 0}],
        [{k: v for k, v in GOOD.items() if k != "phone"}],
        [GOOD, GOOD],  # duplicate id
    ],
)
def test_bad_catalog_raises(bad):
    with pytest.raises(CatalogError):
        parse_catalog({"parts": bad})


def test_load_parts_from_path(tmp_path: Path):
    p = tmp_path / "parts.yaml"
    p.write_text(yaml.safe_dump({"parts": [GOOD]}))
    assert load_parts(p).parts[0].id == "a"


# --- purity -------------------------------------------------------------------------------


def test_domain_modules_import_only_contracts_and_stdlib():
    import sys

    domain_dir = Path(triage.__file__).parent
    for path in domain_dir.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                ok = top in ("romulus_contracts", *sys.stdlib_module_names)
                assert ok or name.startswith("romulus_cloud.domain"), f"{path.name} imports {name}"
