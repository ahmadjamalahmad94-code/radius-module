"""Regression: the migration maps each card BATCH's from-first-connection time
budget («صلاحية الكارت بعد أول اتصال») from the RIGHT source field, per batch.

Live bug: every migrated batch showed «مدة البطاقة: 1 شهر» because the mapping
read the profile's CALENDAR validity (exp_unit/exp_unit_val — uniformly 1 month)
instead of the per-batch from-first-connect budget. The correct source is the
per-group FreeRADIUS time attribute (radgroupreply Session-Timeout, in seconds),
or a profiles session-time column — distinct per batch: امواج البحر=3h,
«5 دقايق ابو العبد»=10min, «اوتو نص ساعة»=30min, «ساعة»=90min…

These tests prove each batch maps to its OWN duration and NONE defaults to the
calendar month, even though every profile carries a uniform exp_unit=1 month.
"""
from __future__ import annotations

from app.radius.services.migration import mapping
from app.radius.services.migration.model import SectionMatch, SourceDataset, SourceTable
from app.radius.services.migration.sections import SEC_BATCHES, norm_key


# Session-Timeout (seconds) per group — the real per-batch budget.
_TIMEOUTS = {
    "امواج البحر": "10800",        # 3 hours
    "5 دقايق ابو العبد": "600",    # 10 minutes
    "اوتو نص ساعة": "1800",        # 30 minutes
    "ساعة": "5400",                # 90 minutes
}


def _adv_dataset() -> SourceDataset:
    profs = list(_TIMEOUTS.keys())
    # profiles ALL carry a uniform CALENDAR validity of 1 month (exp_unit=1,
    # exp_unit_val=3=months) — the field the old mapping wrongly read. The
    # correct budget must come from radgroupreply, NOT this.
    profiles = SourceTable(
        name="profiles",
        columns=["id", "profile_name", "price", "exp_unit", "exp_unit_val"],
        rows=[{"id": str(i + 1), "profile_name": p, "price": "1",
               "exp_unit": "1", "exp_unit_val": "3"} for i, p in enumerate(profs)],
    )
    card_users = SourceTable(
        name="card_users",
        columns=["id", "year", "num_ser", "profile", "price", "created_by"],
        rows=[{"id": str(100 + i), "year": "2024", "num_ser": str(i + 1),
               "profile": str(i + 1), "price": "1", "created_by": ""}
              for i in range(len(profs))],
    )
    rep_cards = SourceTable(
        name="rep_cards",
        columns=["year", "num_ser", "name_ser"],
        rows=[{"year": "2024", "num_ser": str(i + 1), "name_ser": p}
              for i, p in enumerate(profs)],
    )
    radcheck = SourceTable(
        name="radcheck",
        columns=["username", "attribute", "value", "is_card"],
        rows=[{"username": f"c{i}", "attribute": "Cleartext-Password",
               "value": "p", "is_card": "1"} for i in range(len(profs))],
    )
    radusergroup = SourceTable(
        name="radusergroup",
        columns=["username", "groupname", "id_card"],
        rows=[{"username": f"c{i}", "groupname": p, "id_card": str(100 + i)}
              for i, p in enumerate(profs)],
    )
    # radgroupreply carries Session-Timeout per group = the real budget.
    radgroupreply = SourceTable(
        name="radgroupreply",
        columns=["groupname", "attribute", "value"],
        rows=[{"groupname": p, "attribute": "Session-Timeout", "value": secs}
              for p, secs in _TIMEOUTS.items()],
    )
    return SourceDataset(
        fmt="sql_dump",
        tables=[profiles, card_users, rep_cards, radcheck, radusergroup,
                radgroupreply],
    )


def _batch_candidates(ds=None):
    ds = ds or _adv_dataset()
    match = SectionMatch(section=SEC_BATCHES, source_table="card_users",
                         recognized_as="adv_card_users_batch")
    return {c.natural_key: c for c in mapping.build_candidates(ds, match)}


def test_each_batch_maps_its_own_from_first_connect_budget():
    cands = _batch_candidates()
    expected = {
        "امواج البحر": (3, "hours"),
        "5 دقايق ابو العبد": (10, "minutes"),
        "اوتو نص ساعة": (30, "minutes"),
        "ساعة": (90, "minutes"),
    }
    for name, (val, unit) in expected.items():
        c = cands[norm_key(name)]
        assert c.fields.get("count_from_first_connect") is True, name
        assert c.fields.get("time_value") == val, (name, c.fields.get("time_value"))
        assert c.fields.get("time_unit") == unit, (name, c.fields.get("time_unit"))


def test_no_batch_defaults_to_calendar_month():
    # Every profile carries exp_unit=1 month, yet NO batch may map to months.
    cands = _batch_candidates()
    for c in cands.values():
        assert c.fields.get("time_unit") != "months", c.source_ref
        assert not (c.fields.get("time_value") == 1
                    and c.fields.get("time_unit") == "months"), c.source_ref


def test_waves_batch_is_three_hours():
    # The concrete owner case: امواج البحر → 3h (10800s), count-from-first ON.
    waves = _batch_candidates()[norm_key("امواج البحر")]
    assert waves.fields["time_value"] == 3
    assert waves.fields["time_unit"] == "hours"
    assert waves.fields["count_from_first_connect"] is True
    # 3h in seconds == 10800 (what card_accounting.budget_seconds will compute).
    from app.radius.services.card_accounting import budget_seconds
    assert budget_seconds(time_value=3, time_unit="hours") == 10800


def test_group_session_seconds_reads_session_timeout():
    secs = mapping._source_group_session_seconds(_adv_dataset())
    assert secs[norm_key("امواج البحر")] == 10800
    assert secs[norm_key("5 دقايق ابو العبد")] == 600


def test_calendar_only_profile_yields_no_budget():
    # A profile with ONLY a calendar exp_unit (no Session-Timeout, no session
    # column) must yield NO from-first-connect budget — never a month.
    ds = SourceDataset(fmt="sql_dump", tables=[
        SourceTable(name="profiles",
                    columns=["id", "profile_name", "exp_unit", "exp_unit_val"],
                    rows=[{"id": "9", "profile_name": "تقويم فقط",
                           "exp_unit": "1", "exp_unit_val": "3"}]),
        SourceTable(name="card_users",
                    columns=["id", "year", "num_ser", "profile"],
                    rows=[{"id": "9", "year": "2024", "num_ser": "9",
                           "profile": "9"}]),
    ])
    cands = _batch_candidates(ds)
    assert cands, "expected the batch to be produced"
    # The batch exists but carries NO time budget (blank), never a month.
    for cand in cands.values():
        assert not cand.fields.get("time_unit"), cand.fields.get("time_unit")
        assert not cand.fields.get("time_value"), cand.fields.get("time_value")


def test_seconds_to_value_unit_normalization():
    assert mapping._seconds_to_value_unit(10800) == (3, "hours")
    assert mapping._seconds_to_value_unit(600) == (10, "minutes")
    assert mapping._seconds_to_value_unit(5400) == (90, "minutes")
    assert mapping._seconds_to_value_unit(86400) == (1, "days")
    assert mapping._seconds_to_value_unit(0) == (0, "")
