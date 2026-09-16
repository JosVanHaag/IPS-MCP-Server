"""Tests für den lesenden Pfad von ``ips_call``.

Ohne ihn ist ein schreibgeschützter Server blinder als noetig: ``ips_call`` war das
einzige Tool fuer Funktionen ohne eigenes Werkzeug, und es lag komplett hinter dem
Write-Gate. Damit waren ``IPS_GetEvent``, ``AC_GetLoggedValues`` und
``IPS_GetConfiguration`` auch dann unerreichbar, wenn nur gelesen werden sollte.

⚠️ Die Sicherheitsaussage haengt an **einer** Annahme: dass "Get"/"Exists" im Namen
wirklich "liest" bedeutet. Das gilt fuer die dokumentierte Kern-API, nicht fuer
Modulfunktionen fremder Autoren — deren Praefix gehoert nicht IP-Symcon. Der Test
``test_fremde_modulfunktion_bleibt_gesperrt`` ist deshalb der wichtigste hier.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from ipsymcon_mcp import server
from ipsymcon_mcp.server import CallInput, ips_call


def _run(coro):
    return asyncio.run(coro)


def _client_returning(value):
    fake = AsyncMock()
    fake.call.return_value = value
    return fake


@pytest.fixture(autouse=True)
def single_instance(monkeypatch):
    """Einfaches Setup ohne YAML, damit nur der globale Schalter zaehlt."""
    monkeypatch.delenv("IPS_INSTANCES_FILE", raising=False)
    monkeypatch.setenv("IPS_URL", "http://127.0.0.1:3777/api/")


# --- Die Einstufung ----------------------------------------------------------


@pytest.mark.parametrize("method", [
    "IPS_GetEvent", "IPS_GetObject", "IPS_ObjectExists", "IPS_EventExists",
    "AC_GetLoggedValues", "AC_GetAggregatedValues",
    "MC_GetModuleList", "SC_GetShutterControl", "Sys_GetURLContent",
    "GetValue", "GetValueBoolean", "GetValueFormatted",
])
def test_lesende_kernfunktionen_erkannt(method):
    assert server._is_read_only_method(method) is True, method


@pytest.mark.parametrize("method", [
    "IPS_SetName", "IPS_CreateScript", "IPS_DeleteObject", "IPS_RunScript",
    "IPS_ApplyChanges", "IPS_SetEventAction", "AC_DeleteVariableData",
    "SetValue", "SetValueBoolean", "RequestAction", "RequestActionEx",
    "MC_ReloadModule", "IPS_RunScriptText",
])
def test_schreibende_funktionen_bleiben_gesperrt(method):
    assert server._is_read_only_method(method) is False, method


def test_fremde_modulfunktion_bleibt_gesperrt():
    """Der eigentliche Schutz: fremde Praefixe geniessen keinen Vertrauensvorschuss.

    Ein Modul darf seine Funktionen nennen wie es will — ``XYZ_GetAndReset`` waere ein
    zulaessiger Name fuer etwas, das den Zaehler nullt. Die Namenskonvention traegt nur
    dort, wo IP-Symcon den Namen vergibt.
    """
    assert server._is_read_only_method("XYZ_GetAndResetCounter") is False
    assert server._is_read_only_method("FoxESS_GetRegister") is False
    assert server._is_read_only_method("Z2M_GetState") is False


@pytest.mark.parametrize("method", [
    "IPS_GetConfiguration", "IPS_GetSnapshot", "IPS_GetSnapshotChanges",
    "WFC_GetSnapshotChanges", "WFC_GetSnapshotChangesEx",
])
def test_zugangsdaten_tragende_leser_bleiben_gesperrt(method):
    """Diese Funktionen lesen nur -- und muessen trotzdem hinter dem Gate bleiben.

    Sie liefern Instanzkonfigurationen aus, und die tragen regelmaessig Zugangsdaten von
    Integrationen. `ips_export_subtree` haelt sie aus demselben Grund per Vorgabe zurueck
    (`include_configuration=False`). Ein Lesepfad, der sie durchlaesst, macht genau diese
    Schutzmassnahme wieder auf -- `IPS_GetSnapshot` liefert den ganzen Baum samt aller
    Konfigurationen in einem einzigen Aufruf.
    """
    assert server._is_read_only_method(method) is False, method


def test_kernpraefix_ohne_leseverb_bleibt_gesperrt():
    """Praefix allein reicht nicht — das Verb muss auch stimmen."""
    assert server._is_read_only_method("IPS_SetProperty") is False
    assert server._is_read_only_method("KNX_WriteGroupValue") is False


# --- Das Tor ------------------------------------------------------------------


def test_schreibender_call_ohne_write_flag_verweigert(monkeypatch):
    """Kontrolle — der Schutz darf durch die Aenderung nicht loecherig geworden sein."""
    monkeypatch.setenv("IPS_ENABLE_WRITE", "false")
    fake = _client_returning(True)
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_call(CallInput(method="IPS_SetName", params=[10002, "neu"])))
    assert out == server.WRITE_DISABLED_MSG
    fake.call.assert_not_awaited(), "Bei verweigertem Aufruf darf IPS gar nicht erst erreicht werden"


def test_schreibender_call_mit_write_flag(monkeypatch):
    monkeypatch.setenv("IPS_ENABLE_WRITE", "true")
    fake = _client_returning(True)
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_call(CallInput(method="IPS_SetName", params=[10002, "neu"])))
    assert json.loads(out)["result"] is True


# --- Die Aufspaltung ips_call_read / ips_call --------------------------------


def test_lesegateway_ohne_write_flag(monkeypatch):
    """ips_call_read liest ohne IPS_ENABLE_WRITE -- der Zweck der Aufspaltung."""
    monkeypatch.setenv("IPS_ENABLE_WRITE", "false")
    fake = _client_returning({"CyclicTimeValue": 60})
    with patch.object(server, "_client", return_value=fake):
        out = _run(server.ips_call_read(CallInput(method="IPS_GetEvent", params=[10001])))
    assert json.loads(out)["result"]["CyclicTimeValue"] == 60


def test_lesegateway_weist_schreibmethode_ab_auch_mit_write_flag(monkeypatch):
    """Der wichtigste Test: die Grenze haengt am Werkzeug, nicht am Schalter.

    Waere ips_call_read nur durch das Write-Gate geschuetzt, waere die Aufspaltung
    wirkungslos, sobald geschrieben werden darf -- und genau dann soll sie tragen.
    """
    monkeypatch.setenv("IPS_ENABLE_WRITE", "true")
    fake = _client_returning(True)
    with patch.object(server, "_client", return_value=fake):
        out = _run(server.ips_call_read(CallInput(method="IPS_DeleteObject", params=[10002])))
    assert "only accepts reading functions" in out
    fake.call.assert_not_awaited(), "Eine abgewiesene Methode darf IPS nicht erreichen"


def test_lesegateway_weist_zugangsdatentraeger_ab(monkeypatch):
    """Auch hier gilt die Ausnahmeliste -- sonst waere sie ueber das neue Tor umgehbar."""
    monkeypatch.setenv("IPS_ENABLE_WRITE", "false")
    fake = _client_returning({})
    with patch.object(server, "_client", return_value=fake):
        out = _run(server.ips_call_read(CallInput(method="IPS_GetSnapshot", params=[])))
    assert "only accepts reading functions" in out
    fake.call.assert_not_awaited()


def test_schreibgateway_verlangt_wieder_immer_das_flag(monkeypatch):
    """ips_call hat die urspruengliche Semantik zurueck: Gate fuer alles."""
    monkeypatch.setenv("IPS_ENABLE_WRITE", "false")
    fake = _client_returning({})
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_call(CallInput(method="IPS_GetEvent", params=[10001])))
    assert out == server.WRITE_DISABLED_MSG
    fake.call.assert_not_awaited()
