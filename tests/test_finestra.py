# StellaTerm, le prove con le finestre wx, sul desktop nascosto: lo specchio dell'output e la finestra intera.
# Autori: Gabriele Battaglia (IZ4APU) & ClaudIA (Claude Opus 5.5, UltraCode).
# 22/10/2026, 0.5.0: nascono con la prima versione del repository.

import json
import os
import time

import pytest
import wx

import stellaterm
from stellaterm import OutputMirror, StellaTermFrame, u16len
from terminal_engine import Snapshot


def pompa(condizione, secondi=10.0):
    """Fa girare gli eventi di wx finche' la condizione diventa vera, o finche' scade il tempo."""
    fine = time.monotonic() + secondi
    while time.monotonic() < fine:
        wx.GetApp().Yield(True)
        if condizione():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def controllo(app):
    cornice = wx.Frame(None)
    ctrl = wx.TextCtrl(cornice, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
    yield ctrl
    cornice.Destroy()


def istantanea(righe, prima=0, epoca=0, inizio=0):
    return Snapshot(list(righe), prima, epoca, inizio)


def test_specchio_aggiorna_solo_le_righe_cambiate(controllo):
    specchio = OutputMirror(controllo)
    righe = [f"riga {i}" for i in range(10)]
    passi = [
        (righe, 0),
        ([*righe, "riga 10", "riga 11"], 0),
        ([*righe, "riga 10", "E:\\git>dir"], 0),
        ([*([*righe, "riga 10", "E:\\git>dir"])[3:], "nuova"], 3),
        ([*([*righe, "riga 10", "E:\\git>dir"])[3:], "nuova", "emoji 😀 qui", "caffè dopo"], 3),
        ([*([*righe, "riga 10", "E:\\git>dir"])[5:], "nuova", "emoji 😀 qui", "caffè dopo", "fine"], 5),
    ]
    for linee, prima in passi:
        specchio.update(istantanea(linee, prima), keep_reading_position=False)
        assert controllo.GetValue() == "\n".join(linee)
    # Dopo un cls l'epoca cambia e si riscrive tutto
    specchio.update(istantanea(["E:\\git>"], 20, 1), keep_reading_position=False)
    assert controllo.GetValue() == "E:\\git>"


def test_specchio_tiene_la_riga_di_chi_legge(controllo):
    specchio = OutputMirror(controllo)
    righe = [f"riga {i}" for i in range(10)]
    specchio.update(istantanea(righe), keep_reading_position=False)
    pos = specchio.position_of_line(5)
    controllo.SetSelection(pos, pos + len("riga 5"))
    # Tre righe escono in testa per il tetto e ne arrivano tre nuove in fondo
    nuove = [*righe[3:], "riga 10", "riga 11", "riga 12"]
    specchio.update(istantanea(nuove, 3), keep_reading_position=True)
    da, a = controllo.GetSelection()
    assert controllo.GetRange(da, a) == "riga 5"


def test_posizione_delle_righe_dopo_un_emoji(controllo):
    specchio = OutputMirror(controllo)
    righe = ["prima \U0001f600 riga", "seconda riga", "terza"]
    specchio.update(istantanea(righe), keep_reading_position=False)
    pos = specchio.position_of_line(1)
    assert controllo.GetRange(pos, pos + u16len("seconda riga")) == "seconda riga"


@pytest.fixture
def finestra(app, tmp_path, monkeypatch):
    monkeypatch.setattr(stellaterm, "cartella_applicazione", lambda: str(tmp_path))
    cornice = StellaTermFrame(initial_cwd=str(tmp_path))
    yield cornice
    if cornice:
        cornice.Close(force=True)
        pompa(lambda: not cornice, 5.0)


def test_finestra_intera_con_cmd_vero(finestra, tmp_path, suoni_muti):
    assert pompa(lambda: finestra.engine.is_at_shell_prompt()), "il prompt di cmd non arriva"
    assert pompa(lambda: "apertura" in suoni_muti), "il suono di avvio non parte dal riscaldamento"
    finestra._set_command_text("echo ciao finestra")
    finestra._on_input_enter()
    assert pompa(lambda: "terminata" in suoni_muti), "la fine del comando non arriva alla finestra"
    assert finestra.output_ctrl.GetValue() == "\n".join(finestra.engine.get_snapshot().lines)
    pos = finestra.output_ctrl.GetInsertionPoint()
    assert finestra.output_ctrl.GetRange(pos, pos + len("ciao finestra")) == "ciao finestra"
    assert finestra.settings_mgr.history() == {"echo ciao finestra": 1}

    # @st set risponde nell'output, con il suo suono e senza separatori
    finestra._set_command_text("@st set")
    finestra._on_input_enter()
    testo = finestra.output_ctrl.GetValue()
    assert "Parametri attuali di StellaTerm" in testo
    assert "===" not in testo
    assert suoni_muti[-1] == "mostra"

    # Alla chiusura la cronologia finisce nel file della cartella del programma
    finestra.Close(force=True)
    assert pompa(lambda: not finestra, 5.0)
    with open(os.path.join(tmp_path, "stellaterm_settings.json"), encoding="utf-8") as f:
        salvate = json.load(f)
    assert salvate["history"] == {"echo ciao finestra": 1, "@st set": 1}


def test_suggerimento_scartato_accettato_e_invio(finestra):
    assert pompa(lambda: finestra.engine.is_at_shell_prompt())
    finestra.settings_mgr.use_command("dir /s /b")
    finestra._set_command_text("di")
    finestra._update_inline_autocomplete_from_typing()
    assert finestra._has_active_suggestion_selection()
    assert finestra.input_ctrl.GetStringSelection() == "r /s /b"
    # Invio esegue lo scritto: la coda si scarta
    evento = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
    evento.SetKeyCode(wx.WXK_RETURN)
    inviati = []
    finestra.engine.send_command = inviati.append
    finestra._on_input_key_down(evento)
    assert inviati == ["di"]
    # Freccia Destra accetta la voce cosi' com'e' memorizzata, maiuscole comprese
    finestra._set_command_text("DI")
    finestra._update_inline_autocomplete_from_typing()
    destra = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
    destra.SetKeyCode(wx.WXK_RIGHT)
    finestra._on_input_key_down(destra)
    assert finestra._get_command_text() == "dir /s /b"


def test_il_prefisso_rovinato_si_ripara(finestra):
    finestra._set_command_text("dir")
    prefisso = finestra._get_input_prefix()
    finestra.input_ctrl.SetValue("rovinato\nper intero")
    assert finestra.input_ctrl.GetValue() == prefisso + "per intero"
    finestra.input_ctrl.SetValue(prefisso + "copy a\nb")
    assert finestra.input_ctrl.GetValue() == prefisso + "copy a b"
