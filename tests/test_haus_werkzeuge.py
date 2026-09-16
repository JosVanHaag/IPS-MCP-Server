"""Tests fuer die drei ergaenzten Werkzeuge und die Parent-Vorpruefung.

Jedes davon schliesst eine Luecke, die vorher ein lokales Shellscript ausgefuellt hat:
Teilstringsuche ueber den ganzen Baum, Archivzugriff mit Statuspruefung, und Links.
Dazu die Vorpruefung des Parents, ohne die ein fehlgeschlagenes Anlegen ein namenloses
Objekt in der Wurzel zuruecklaesst.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, call, patch

import pytest

from ipsymcon_mcp import server
from ipsymcon_mcp.server import (
    CreateLinkInput,
    FindObjectsInput,
    GetLoggedValuesInput,
    ips_create_link,
    ips_find_objects,
    ips_get_logged_values,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def single_instance(monkeypatch):
    monkeypatch.delenv("IPS_INSTANCES_FILE", raising=False)
    monkeypatch.setenv("IPS_URL", "http://127.0.0.1:3777/api/")


# Ein Snapshot, wie IPS ihn liefert -- inklusive eines Konfigurationsblocks, der
# NICHT nach aussen dringen darf
SNAPSHOT = {
    "objects": {
        "ID10": {"name": "Praesenz aktiv", "ident": "PraesenzAktiv", "type": 2, "parentID": 1},
        "ID11": {"name": "Deckenlampe", "ident": "", "type": 2, "parentID": 1},
        "ID12": {"name": "Wohnzimmer", "ident": "", "type": 0, "parentID": 0},
        "ID13": {"name": "Sonstiges", "ident": "praesenz_intern", "type": 3, "parentID": 1},
    },
    "server": {"password": "darf-nicht-durchsickern"},
}


def _client_with(*, value=None, side_effect=None):
    fake = AsyncMock()
    if side_effect is not None:
        fake.call.side_effect = side_effect
    else:
        fake.call.return_value = value
    return fake


# --- ips_find_objects ---------------------------------------------------------


def test_suche_findet_name_und_ident():
    """Der Ident ist oft das Einzige, was man hat -- er muss mitdurchsucht werden."""
    fake = _client_with(value=SNAPSHOT)
    with patch.object(server, "_client", return_value=fake):
        out = json.loads(_run(ips_find_objects(FindObjectsInput(query="praesenz"))))
    ids = sorted(o["id"] for o in out["objects"])
    assert ids == [10, 13], "10 trifft ueber den Ident, 13 ueber den Ident-Teilstring"
    assert out["count"] == 2


def test_suche_ist_case_insensitiv():
    fake = _client_with(value=SNAPSHOT)
    with patch.object(server, "_client", return_value=fake):
        out = json.loads(_run(ips_find_objects(FindObjectsInput(query="DECKENLAMPE"))))
    assert [o["id"] for o in out["objects"]] == [11]


def test_suche_filtert_nach_typ():
    fake = _client_with(value=SNAPSHOT)
    with patch.object(server, "_client", return_value=fake):
        out = json.loads(_run(ips_find_objects(FindObjectsInput(query="praesenz", object_type=3))))
    assert [o["id"] for o in out["objects"]] == [13]


def test_suche_gibt_keine_konfiguration_preis():
    """Der Snapshot traegt Zugangsdaten -- nur die fuenf harmlosen Felder duerfen raus."""
    fake = _client_with(value=SNAPSHOT)
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_find_objects(FindObjectsInput(query="e")))
    assert "darf-nicht-durchsickern" not in out
    for obj in json.loads(out)["objects"]:
        assert set(obj) == {"id", "name", "ident", "type", "parent_id"}


def test_suche_meldet_abschneiden():
    fake = _client_with(value=SNAPSHOT)
    with patch.object(server, "_client", return_value=fake):
        out = json.loads(_run(ips_find_objects(FindObjectsInput(query="e", limit=1))))
    assert out["truncated"] is True
    assert len(out["objects"]) == 1
    assert out["count"] > 1, "count zaehlt alle Treffer, nicht nur die ausgelieferten"


# --- ips_get_logged_values ----------------------------------------------------


def test_archiv_dreht_auf_chronologisch_und_rechnet_haltedauer():
    """IPS liefert neueste zuerst -- fuer jede Frage nach einem Ablauf ist das rueckwaerts."""
    rows = [
        {"TimeStamp": 2000, "Value": 30},
        {"TimeStamp": 1600, "Value": 20},
        {"TimeStamp": 1000, "Value": 10},
    ]
    fake = _client_with(side_effect=[[59422], True, rows])
    with patch.object(server, "_client", return_value=fake):
        out = json.loads(_run(ips_get_logged_values(GetLoggedValuesInput(variable_id=10001))))
    assert [v["value"] for v in out["values"]] == [10, 20, 30]
    assert out["values"][0]["held_seconds"] == 600, "1000 stand bis 1600"
    assert out["values"][1]["held_seconds"] == 400
    assert out["archive_id"] == 59422


def test_archiv_meldet_fehlendes_logging_statt_leerer_liste():
    """Der eigentliche Wert der Vorabpruefung: 'nicht archiviert' ist nicht 'keine Werte'."""
    fake = _client_with(side_effect=[[59422], False])
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_get_logged_values(GetLoggedValuesInput(variable_id=10001)))
    assert "not archived" in out
    assert fake.call.await_count == 2, "Ohne Logging darf gar nicht erst gelesen werden"


def test_archiv_meldet_fehlende_instanz():
    fake = _client_with(side_effect=[[]])
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_get_logged_values(GetLoggedValuesInput(variable_id=10001)))
    assert "no Archive Control instance" in out


# --- ips_create_link und die Parent-Vorpruefung --------------------------------


def test_link_ohne_write_flag_verweigert(monkeypatch):
    monkeypatch.setenv("IPS_ENABLE_WRITE", "false")
    fake = _client_with(value=True)
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_create_link(CreateLinkInput(target_id=10001, parent_id=10002, name="X")))
    assert out == server.WRITE_DISABLED_MSG
    fake.call.assert_not_awaited()


def test_link_legt_nichts_an_wenn_parent_fehlt(monkeypatch):
    """Der Kern der Vorpruefung: kein halbfertiges Objekt in der Wurzel."""
    monkeypatch.setenv("IPS_ENABLE_WRITE", "true")
    fake = _client_with(side_effect=[False])
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_create_link(CreateLinkInput(target_id=10001, parent_id=99999, name="X")))
    assert "does not exist" in out
    assert fake.call.await_args_list == [call("IPS_ObjectExists", [99999])], \
        "Nach der Absage darf kein IPS_CreateLink mehr folgen"


def test_link_legt_nichts_an_wenn_ziel_fehlt(monkeypatch):
    monkeypatch.setenv("IPS_ENABLE_WRITE", "true")
    fake = _client_with(side_effect=[True, False])
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_create_link(CreateLinkInput(target_id=99999, parent_id=10002, name="X")))
    assert "link target 99999 does not exist" in out
    assert fake.call.await_count == 2


def test_link_vollstaendige_reihenfolge(monkeypatch):
    monkeypatch.setenv("IPS_ENABLE_WRITE", "true")
    fake = _client_with(side_effect=[True, True, 4242, None, None, None])
    with patch.object(server, "_client", return_value=fake):
        out = json.loads(_run(ips_create_link(
            CreateLinkInput(target_id=10001, parent_id=10002, name="Leistung"))))
    assert out == {"link_id": 4242, "name": "Leistung", "parent_id": 10002,
                   "target_id": 10001, "ok": True}
    assert fake.call.await_args_list == [
        call("IPS_ObjectExists", [10002]),
        call("IPS_ObjectExists", [10001]),
        call("IPS_CreateLink", []),
        call("IPS_SetLinkTargetID", [4242, 10001]),
        call("IPS_SetParent", [4242, 10002]),
        call("IPS_SetName", [4242, "Leistung"]),
    ]
