"""FIX 2 regression: the migration import maps the source accounting mode +
validity budget onto the card BATCH.

The adv "Hobe Hub" source stamps, per card batch (card_users row → profile):
  • «طريقة الإحتساب» = «من اول إتصال»  → count_from_first_connect
  • «صلاحية الكارت بعد أول اتصال»       → time budget (exp_unit + exp_unit_val)

Before the fix, _build_adv_card_users_batches carried only name/plan/price/
count/manager, so migrated batches lost the mode + budget entirely and the
checker had nothing to resolve from. This proves the batch candidate now
carries time_value/time_unit + count_from_first_connect faithfully.

Reference: batch «امواج البحر» → profile exp_unit=3, exp_unit_val=5(hours) →
time_value=3, time_unit='hours', count_from_first_connect=True.
"""
from __future__ import annotations

from app.radius.services.migration import mapping
from app.radius.services.migration.model import SectionMatch, SourceDataset, SourceTable
from app.radius.services.migration.sections import SEC_BATCHES


def _adv_dataset() -> SourceDataset:
    profiles = SourceTable(
        name="profiles",
        columns=["id", "profile_name", "price", "exp_unit", "exp_unit_val"],
        rows=[
            # «امواج البحر»: 3 hours (exp_unit_val=5 → hours).
            {"id": "7", "profile_name": "امواج البحر", "price": "5",
             "exp_unit": "3", "exp_unit_val": "5"},
            # «5 دقايق ابو العبد»: 10 minutes (exp_unit_val=6 → minutes).
            {"id": "8", "profile_name": "5 دقايق ابو العبد", "price": "1",
             "exp_unit": "10", "exp_unit_val": "6"},
        ],
    )
    card_users = SourceTable(
        name="card_users",
        columns=["id", "year", "num_ser", "profile", "price", "created_by"],
        rows=[
            {"id": "100", "year": "2024", "num_ser": "1", "profile": "7",
             "price": "5", "created_by": ""},
            {"id": "101", "year": "2024", "num_ser": "2", "profile": "8",
             "price": "1", "created_by": ""},
        ],
    )
    # rep_cards names the series → gives the batch its Arabic name.
    rep_cards = SourceTable(
        name="rep_cards",
        columns=["year", "num_ser", "name_ser"],
        rows=[
            {"year": "2024", "num_ser": "1", "name_ser": "امواج البحر"},
            {"year": "2024", "num_ser": "2", "name_ser": "5 دقايق ابو العبد"},
        ],
    )
    # radcheck (is_card=1) + radusergroup give each batch a card count.
    radcheck = SourceTable(
        name="radcheck",
        columns=["username", "attribute", "value", "is_card"],
        rows=[
            {"username": "c1", "attribute": "Cleartext-Password", "value": "p", "is_card": "1"},
            {"username": "c2", "attribute": "Cleartext-Password", "value": "p", "is_card": "1"},
        ],
    )
    radusergroup = SourceTable(
        name="radusergroup",
        columns=["username", "groupname", "id_card"],
        rows=[
            {"username": "c1", "groupname": "امواج البحر", "id_card": "100"},
            {"username": "c2", "groupname": "5 دقايق ابو العبد", "id_card": "101"},
        ],
    )
    return SourceDataset(
        fmt="sql_dump",
        tables=[profiles, card_users, rep_cards, radcheck, radusergroup],
    )


def _batch_candidates():
    ds = _adv_dataset()
    match = SectionMatch(
        section=SEC_BATCHES, source_table="card_users",
        recognized_as="adv_card_users_batch",
    )
    cands = mapping.build_candidates(ds, match)
    return {c.natural_key: c for c in cands}


def test_waves_batch_maps_from_first_connect_and_3h_budget():
    from app.radius.services.migration.sections import norm_key
    cands = _batch_candidates()
    waves = cands[norm_key("امواج البحر")]
    assert waves.fields.get("count_from_first_connect") is True
    assert waves.fields.get("time_value") == 3
    assert waves.fields.get("time_unit") == "hours"


def test_ten_minute_batch_maps_minutes_budget():
    from app.radius.services.migration.sections import norm_key
    cands = _batch_candidates()
    abu = cands[norm_key("5 دقايق ابو العبد")]
    assert abu.fields.get("count_from_first_connect") is True
    assert abu.fields.get("time_value") == 10
    assert abu.fields.get("time_unit") == "minutes"


def test_profile_validity_map_decodes_adv_units():
    ds = _adv_dataset()
    vmap = mapping._source_profile_validity_map(ds)
    assert vmap["7"] == (3, "hours")
    assert vmap["8"] == (10, "minutes")


def test_no_validity_profile_yields_no_budget():
    # A profile with exp_unit=0 must NOT invent a budget.
    ds = SourceDataset(fmt="sql_dump", tables=[
        SourceTable(name="profiles",
                    columns=["id", "profile_name", "exp_unit", "exp_unit_val"],
                    rows=[{"id": "9", "profile_name": "بلا صلاحية",
                           "exp_unit": "0", "exp_unit_val": "5"}]),
    ])
    vmap = mapping._source_profile_validity_map(ds)
    assert "9" not in vmap
