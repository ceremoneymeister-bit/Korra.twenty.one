"""A late answer about an old CRM access must never land in a newer connection.

Synthetic: ``cc.probe`` is replaced, so there is no CRM network and no real key.
A probe that «returns late» is one that changes the stored connection first.
"""

from __future__ import annotations

import pytest

from korra_cli import crm_connection as cc
from korra_cli import crm_readers as cr
from korra_cli import crm_sales

OLD = {"type": "amocrm", "domain": "review.amocrm.ru", "token": "synthetic-old-key"}
NEW = {**OLD, "token": "synthetic-new-key", "settings": {"pipeline_id": "200"}}
OLD_FOUND = {"user": "Старый доступ", "pipelines": [{"id": "100", "name": "Старая воронка"}]}
NEW_FOUND = {"user": "Новый доступ", "pipelines": [{"id": "200", "name": "Новая воронка"}]}


def _by_token(monkeypatch, **overrides):
    """A probe that answers by key; ``overrides[token]`` may be a callable run first."""
    answers = {OLD["token"]: OLD_FOUND, NEW["token"]: NEW_FOUND}

    def probe(conn):
        hook = overrides.get(conn["token"])
        if hook:
            hook(conn)
        return answers[conn["token"]]

    monkeypatch.setattr(cc, "probe", probe)


@pytest.mark.parametrize("old_result", ["bad_key", "success"])
def test_delayed_check_of_old_key_cannot_overwrite_new_connection(tmp_path, monkeypatch, old_result):
    monkeypatch.setattr(cc, "probe", lambda conn: OLD_FOUND)
    assert cc.save(OLD, root=tmp_path)["ok"]

    def delayed_probe(conn):
        if conn["token"] == NEW["token"]:
            return NEW_FOUND
        # Another request successfully replaces the key before this old check returns.
        assert cc.save(NEW, root=tmp_path)["ok"]
        assert cc.load(tmp_path)["last_check"]["ok"] is True
        if old_result == "bad_key":
            raise cr.CrmError("bad_key")
        return OLD_FOUND

    monkeypatch.setattr(cc, "probe", delayed_probe)
    cc.recheck(root=tmp_path)
    saved = cc.load(tmp_path)
    assert saved["token"] == NEW["token"]
    if old_result == "bad_key":
        scheduled = []
        monkeypatch.setattr(crm_sales, "_spawn", lambda target: scheduled.append(target))
        crm_sales.reset()
        try:
            card = crm_sales.sales_section(root=tmp_path, wait=0)
            print("last_check=", saved["last_check"], "card_status=", card["status"],
                  "card_error=", card.get("error", {}).get("code"), "scheduled=", len(scheduled))
            assert saved["last_check"]["ok"] is True
            assert card["status"] != "error"
            assert len(scheduled) == 1
        finally:
            crm_sales.reset()
    else:
        print("account=", saved["account"], "settings=", saved["settings"])
        assert saved["account"]["user"] == NEW_FOUND["user"]
        assert saved["settings"]["pipeline_id"] == "200"


@pytest.mark.parametrize("outcome", ["bad_key", "success"])
def test_discarded_recheck_says_so_instead_of_judging_the_new_key(tmp_path, monkeypatch, outcome):
    monkeypatch.setattr(cc, "probe", lambda conn: OLD_FOUND)
    cc.save(OLD, root=tmp_path)

    def late(conn):
        if conn["token"] == NEW["token"]:
            return NEW_FOUND
        cc.save(NEW, root=tmp_path)
        if outcome == "bad_key":
            raise cr.CrmError("bad_key")
        return OLD_FOUND

    monkeypatch.setattr(cc, "probe", late)
    out = cc.recheck(root=tmp_path)
    assert out["ok"] is False and out["error"]["code"] == "superseded"
    assert out["error"]["title"] and out["error"]["message"]
    assert "connection" not in out and "found" not in out


def test_recheck_of_the_same_access_keeps_settings_changed_meanwhile(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "probe", lambda conn: {**OLD_FOUND, "pipelines": [{"id": "100", "name": "A"}, {"id": "101", "name": "B"}]})
    cc.save(OLD, root=tmp_path)

    def late(conn):
        cc.update_settings({"pipeline_id": "101", "stuck_days": 14}, root=tmp_path)
        return {**OLD_FOUND, "user": "Обновлённый", "pipelines": [{"id": "100", "name": "A"}, {"id": "101", "name": "B"}]}

    monkeypatch.setattr(cc, "probe", late)
    out = cc.recheck(root=tmp_path)
    saved = cc.load(tmp_path)
    assert out["ok"] is True
    assert (saved["settings"]["pipeline_id"], saved["settings"]["stuck_days"]) == ("101", 14)
    assert saved["account"]["user"] == "Обновлённый"


def test_recheck_after_disconnect_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "probe", lambda conn: OLD_FOUND)
    cc.save(OLD, root=tmp_path)

    def late(conn):
        cc.disconnect(root=tmp_path)
        raise cr.CrmError("bad_key")

    monkeypatch.setattr(cc, "probe", late)
    cc.recheck(root=tmp_path)
    assert cc.load(tmp_path) is None


def test_slow_save_of_an_old_key_does_not_replace_a_newer_save(tmp_path, monkeypatch):
    results = {}
    _by_token(monkeypatch, **{OLD["token"]: lambda conn: results.setdefault("new", cc.save(NEW, root=tmp_path))})
    out = cc.save(OLD, root=tmp_path)
    assert results["new"]["ok"] is True
    assert out["ok"] is False and out["error"]["code"] == "superseded"
    saved = cc.load(tmp_path)
    assert saved["token"] == NEW["token"]
    assert saved["account"]["user"] == NEW_FOUND["user"] and saved["settings"]["pipeline_id"] == "200"


def test_slow_save_does_not_bring_the_connection_back_after_disconnect(tmp_path, monkeypatch):
    _by_token(monkeypatch, **{OLD["token"]: lambda conn: cc.disconnect(root=tmp_path)})
    out = cc.save(OLD, root=tmp_path)
    assert out["ok"] is False and out["error"]["code"] == "superseded"
    assert cc.load(tmp_path) is None


def test_slow_adopt_of_an_agent_key_does_not_replace_a_newer_save(tmp_path, monkeypatch):
    agent = type("Agent", (), {"profile": "sales", "label": "Продажи"})()
    monkeypatch.setattr(cc, "_agent_keys", lambda: [(agent, dict(OLD))])
    _by_token(monkeypatch, **{OLD["token"]: lambda conn: cc.save(NEW, root=tmp_path)})
    out = cc.adopt("sales", "amocrm", root=tmp_path)
    assert out["ok"] is False and out["error"]["code"] == "superseded"
    assert cc.load(tmp_path)["token"] == NEW["token"]


def test_a_rejected_newer_save_does_not_cancel_a_slow_valid_one(tmp_path, monkeypatch):
    seen = {}

    def reject_meanwhile(conn):
        seen["bad"] = cc.save({**OLD, "token": "rejected-key"}, root=tmp_path)

    answers = {OLD["token"]: OLD_FOUND}

    def probe(conn):
        if conn["token"] == "rejected-key":
            raise cr.CrmError("bad_key")
        reject_meanwhile(conn)
        return answers[conn["token"]]

    monkeypatch.setattr(cc, "probe", probe)
    out = cc.save(OLD, root=tmp_path)
    assert seen["bad"]["ok"] is False and seen["bad"]["error"]["code"] == "bad_key"
    assert out["ok"] is True
    assert cc.load(tmp_path)["token"] == OLD["token"]


def test_stores_do_not_queue_behind_each_other(tmp_path, monkeypatch):
    other = tmp_path / "other"
    _by_token(monkeypatch, **{OLD["token"]: lambda conn: cc.save(NEW, root=other)})
    assert cc.save(OLD, root=tmp_path)["ok"] is True
    assert cc.load(tmp_path)["token"] == OLD["token"] and cc.load(other)["token"] == NEW["token"]


def test_save_over_a_network_failure_does_not_inherit_the_old_keys_account(tmp_path, monkeypatch):
    _by_token(monkeypatch)
    cc.save(OLD, root=tmp_path)
    assert cc.load(tmp_path)["account"]["user"] == OLD_FOUND["user"]

    def down(conn):
        raise cr.CrmError("network")

    monkeypatch.setattr(cc, "probe", down)
    out = cc.save({**NEW, "settings": {}}, root=tmp_path)
    saved = cc.load(tmp_path)
    assert out["ok"] is True and out["warning"]["code"] == "network"
    assert saved["token"] == NEW["token"] and saved["account"] == {}
    assert saved["last_check"]["ok"] is False

    cc.save(NEW, root=tmp_path)
    saved = cc.load(tmp_path)
    assert saved["account"] == {} and saved["last_check"]["code"] == "network"


def test_save_of_the_same_key_after_a_network_failure_keeps_its_account(tmp_path, monkeypatch):
    _by_token(monkeypatch)
    cc.save(OLD, root=tmp_path)

    def down(conn):
        raise cr.CrmError("network")

    monkeypatch.setattr(cc, "probe", down)
    cc.save(OLD, root=tmp_path)
    assert cc.load(tmp_path)["account"]["user"] == OLD_FOUND["user"]


def test_settings_patch_is_checked_against_the_current_connections_pipelines(tmp_path, monkeypatch):
    _by_token(monkeypatch)
    cc.save(OLD, root=tmp_path)
    cc.save({**NEW, "settings": {}}, root=tmp_path)
    with pytest.raises(cc.CrmConnectionError):
        cc.update_settings({"pipeline_id": "100"}, root=tmp_path)
    assert cc.update_settings({"pipeline_id": "200"}, root=tmp_path)["settings"]["pipeline_id"] == "200"


def test_refresh_started_for_an_old_key_never_shows_up_under_the_new_one(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "probe", lambda conn: OLD_FOUND)
    cc.save(OLD, root=tmp_path)
    jobs = []
    monkeypatch.setattr(crm_sales, "_spawn", lambda target: jobs.append(target))
    monkeypatch.setattr(crm_sales, "gather", lambda conn, **kw: {"marker": conn["token"]})
    crm_sales.reset()
    try:
        assert crm_sales.sales_section(root=tmp_path, wait=0)["status"] == "loading"
        old_refresh = jobs.pop()
        monkeypatch.setattr(cc, "probe", lambda conn: NEW_FOUND)
        cc.save(NEW, root=tmp_path)
        assert crm_sales.sales_section(root=tmp_path, wait=0)["status"] == "loading"
        new_refresh = jobs.pop()
        old_refresh()
        card = crm_sales.sales_section(root=tmp_path, wait=0)
        assert card["status"] == "loading" and "marker" not in card
        new_refresh()
        card = crm_sales.sales_section(root=tmp_path, wait=0)
        assert card["status"] == "ok" and card["marker"] == NEW["token"]
    finally:
        crm_sales.reset()


def test_failed_refresh_of_an_old_key_does_not_block_the_new_one(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "probe", lambda conn: OLD_FOUND)
    cc.save(OLD, root=tmp_path)
    jobs = []
    monkeypatch.setattr(crm_sales, "_spawn", lambda target: jobs.append(target))

    def gather(conn, **kw):
        if conn["token"] == OLD["token"]:
            raise cr.CrmError("bad_key")
        return {"marker": conn["token"]}

    monkeypatch.setattr(crm_sales, "gather", gather)
    crm_sales.reset()
    try:
        crm_sales.sales_section(root=tmp_path, wait=0)
        old_refresh = jobs.pop()
        monkeypatch.setattr(cc, "probe", lambda conn: NEW_FOUND)
        cc.save(NEW, root=tmp_path)
        crm_sales.sales_section(root=tmp_path, wait=0)
        new_refresh = jobs.pop()
        old_refresh()
        new_refresh()
        card = crm_sales.sales_section(root=tmp_path, wait=0)
        assert card["status"] == "ok" and "error" not in card
    finally:
        crm_sales.reset()
