# StellaTerm, le prove delle impostazioni, dei nomi dei salvataggi e dei testi mostrati.
# Autori: Gabriele Battaglia (IZ4APU) & ClaudIA (Claude Opus 5.5, UltraCode).
# 22/10/2026, 0.5.0: nascono con la prima versione del repository.

import json
import os
import re

import pytest

import stellaterm
from stellaterm import (
    MAX_HISTORY_COMMANDS,
    RECENT_PROTECTED,
    SettingsManager,
    leggi_percentuale,
    manuale_senza_markdown,
    recupera_comando,
    salva_nome,
    sanitize_settings,
    trim_history,
    u16len,
)


@pytest.fixture
def cartella(tmp_path, monkeypatch):
    """Le impostazioni nascono in una cartella temporanea, mai accanto al programma."""
    monkeypatch.setattr(stellaterm, "cartella_applicazione", lambda: str(tmp_path))
    return tmp_path


def scrivi(percorso, contenuto, codifica="utf-8"):
    with open(percorso, "w", encoding=codifica) as f:
        f.write(contenuto)


def leggi(percorso):
    with open(percorso, encoding="utf-8") as f:
        return json.load(f)


def test_valori_fuori_intervallo_e_infiniti(cartella):
    data = sanitize_settings({"buffer": 1e999, "volume": "x", "size_i": 500, "rgb_i_b": [200, -5, "a"], "history": {"dir": 1e999, "": 3}})
    assert data["buffer"] == stellaterm.MAX_BUFFER_LINES
    assert data["volume"] == 100
    assert data["size_i"] == 72
    assert data["rgb_i_b"] == [100, 0, stellaterm.DEFAULT_SETTINGS["rgb_i_b"][2]]
    assert data["history"] == {"dir": 1}


def test_buffer_con_limite_superiore():
    assert sanitize_settings({"buffer": 999999999})["buffer"] == stellaterm.MAX_BUFFER_LIMIT


def test_i_predefiniti_non_si_toccano():
    data = sanitize_settings(None)
    data["history"]["x"] = 1
    data["rgb_i_b"][0] = 50
    assert stellaterm.DEFAULT_SETTINGS["history"] == {}
    assert stellaterm.DEFAULT_SETTINGS["rgb_i_b"][0] == 11


def test_cronologia_piena_tiene_i_recenti():
    storia = {f"vecchio {i}": 5 for i in range(MAX_HISTORY_COMMANDS)}
    for i in range(RECENT_PROTECTED):
        storia[f"nuovo {i}"] = 1
    ridotta = trim_history(storia)
    assert len(ridotta) == MAX_HISTORY_COMMANDS
    assert all(f"nuovo {i}" in ridotta for i in range(RECENT_PROTECTED))
    assert list(ridotta)[-1] == f"nuovo {RECENT_PROTECTED - 1}"


def test_il_file_nasce_se_manca(cartella):
    gestore = SettingsManager()
    assert os.path.isfile(gestore.filepath)
    assert leggi(gestore.filepath)["volume"] == 100


def test_file_con_bom_si_legge(cartella):
    scrivi(os.path.join(cartella, "stellaterm_settings.json"), json.dumps({"volume": 30, "history": {"cd ..": 3}}), "utf-8-sig")
    gestore = SettingsManager()
    assert gestore.data["volume"] == 30
    assert gestore.load_error == ""


def test_file_illeggibile_messo_da_parte(cartella):
    percorso = os.path.join(cartella, "stellaterm_settings.json")
    scrivi(percorso, '{"volume": 30,, "history": {"cd ..": 3}}')
    gestore = SettingsManager()
    assert gestore.data["volume"] == 100
    assert "messo da parte" in gestore.load_error
    da_parte = [n for n in os.listdir(cartella) if n.startswith("stellaterm_settings.json.illeggibile_")]
    assert len(da_parte) == 1
    with open(os.path.join(cartella, da_parte[0]), encoding="utf-8") as f:
        assert '"cd ..": 3' in f.read()
    # Il salvataggio di chiusura scrive un file nuovo, e la copia messa da parte resta
    assert gestore.save()
    assert os.path.isfile(os.path.join(cartella, da_parte[0]))


def test_due_finestre_non_si_cancellano_a_vicenda(cartella):
    scrivi(os.path.join(cartella, "stellaterm_settings.json"), json.dumps({"volume": 30, "history": {"dir": 4}}))
    a = SettingsManager()
    b = SettingsManager()
    a.set("volume", 55)
    a.use_command("solo in A")
    a.use_command("dir")
    b.use_command("solo in B")
    assert a.save()
    assert b.save()
    finale = leggi(a.filepath)
    assert finale["volume"] == 55
    assert finale["history"] == {"solo in A": 1, "dir": 5, "solo in B": 1}


def test_azzeramento_della_cronologia_arriva_nel_file(cartella):
    scrivi(os.path.join(cartella, "stellaterm_settings.json"), json.dumps({"history": {"dir": 4}}))
    gestore = SettingsManager()
    gestore.reset_history()
    gestore.use_command("dopo")
    assert gestore.save()
    assert leggi(gestore.filepath)["history"] == {"dopo": 1}


def test_salvataggio_non_riuscito(cartella):
    gestore = SettingsManager()
    gestore.filepath = os.path.join(cartella, "non_esiste", "stellaterm_settings.json")
    assert not gestore.save()
    assert gestore.last_error


def test_ordine_della_cronologia_e_la_recenza(cartella):
    gestore = SettingsManager()
    for cmd in ("git status", "dir", "git status", "cd .."):
        gestore.use_command(cmd)
    assert list(gestore.history()) == ["dir", "git status", "cd .."]
    assert gestore.history()["git status"] == 2


def test_nomi_dei_salvataggi(tmp_path):
    ts = "2026-10-22_13-46-34"
    percorso, errore = salva_nome("", str(tmp_path), ts)
    assert percorso == os.path.join(tmp_path, f"stellaterm_buffer_{ts}.txt") and not errore
    assert salva_nome("prova", str(tmp_path), ts)[0] == os.path.join(tmp_path, f"prova_{ts}.txt")
    assert salva_nome("report.md", str(tmp_path), ts)[0] == os.path.join(tmp_path, "report.md")
    scrivi(os.path.join(tmp_path, "log.txt"), "ieri")
    assert salva_nome("log.txt", str(tmp_path), ts)[0] == os.path.join(tmp_path, "log_2.txt")
    percorso, errore = salva_nome("log 22:10", str(tmp_path), ts)
    assert percorso is None and ":" in errore
    assert salva_nome("a?b", str(tmp_path), ts)[0] is None


def test_recupero_del_comando_senza_la_riga_dell_indirizzo():
    assert recupera_comando("E:\\git>\n[3] dir /s", 3, "") == "dir /s"
    assert recupera_comando("E:\\git>\n[", 3, "vecchio") == ""
    assert recupera_comando("E:abc\\git>\n[1] d", 1, "") == "d"
    assert recupera_comando("rovinato", 1, "ultimo") == "ultimo"


def test_manuale_senza_segni_del_markdown():
    righe = manuale_senza_markdown(["# StellaTerm", "## 1. Introduzione", "Il tasto **F1** apre `@st help`.", "* voce", "Vedi [GBUtils](https://github.com/x)."])
    assert righe == ["StellaTerm", "1. Introduzione", "Il tasto F1 apre @st help.", "- voce", "Vedi GBUtils (https://github.com/x)."]


def test_percentuali():
    assert leggi_percentuale("70%") == 70
    assert leggi_percentuale(" 20 ") == 20
    assert leggi_percentuale("12,4") == 12
    for sbagliata in ("inf", "1e999", "101", "-1", "x", "nan"):
        assert leggi_percentuale(sbagliata) is None


def test_posizioni_utf16():
    assert u16len("abc") == 3
    assert u16len("caffè") == 5
    assert u16len("ok \U0001f600") == 5


def test_manuale_senza_separatori_grafici():
    percorso = os.path.join(os.path.dirname(stellaterm.__file__), stellaterm.MANUAL_FILENAME)
    with open(percorso, encoding="utf-8") as f:
        righe = f.read().splitlines()
    for riga in righe:
        assert not re.fullmatch(r"\s*([=\-_*~])\1{2,}\s*", riga), f"separatore grafico nel manuale: {riga!r}"
    assert any(stellaterm.VERSIONE in riga for riga in righe[:10]), "la versione manca in testa al manuale"
