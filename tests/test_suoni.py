# StellaTerm, le prove dei suoni: un preset per evento, nessuna onda quadra, firme e elenco aggiornati.
# Autori: Gabriele Battaglia (IZ4APU) & ClaudIA (Claude Opus 5.5, UltraCode).
# 22/10/2026, 0.5.0: nascono con la mappa dei suoni a un preset per evento.

import json
import os
import re

import GBUtils

import elenco_suoni
import stellaterm

COLLEZIONE = os.path.join(os.path.dirname(GBUtils.__file__), "Acu_Collection.json")
SORGENTE = os.path.join(os.path.dirname(stellaterm.__file__), "stellaterm.py")


def collezione():
    with open(COLLEZIONE, encoding="utf-8") as f:
        return json.load(f)


def test_ogni_preset_esiste_e_non_e_a_onda_quadra():
    dati = collezione()
    for evento, (preset, azione) in stellaterm.SUONI.items():
        assert preset in dati, f"{evento}: il preset {preset} non c'e' nella collezione"
        assert dati[preset].get("kind") != 2, f"{evento}: {preset} e' a onda quadra"
        assert azione.endswith("."), evento


def test_nessun_preset_per_due_eventi():
    dati = collezione()
    preset = [p for p, _a in stellaterm.SUONI.values()]
    assert len(set(preset)) == len(preset)
    impronte = [json.dumps([dati[p].get("score"), dati[p].get("kind"), dati[p].get("adsr")], sort_keys=True) for p in preset]
    assert len(set(impronte)) == len(impronte), "due eventi hanno suoni identici"


def test_ogni_preset_porta_la_firma():
    dati = collezione()
    for preset, _azione in stellaterm.SUONI.values():
        firma = re.search(r"Usato da: ([^.]*)\.", dati[preset]["descrizione"])
        assert firma and "stellaterm" in [n.strip() for n in firma.group(1).split(",")], f"{preset} senza la firma di stellaterm"


def test_ogni_evento_suona_e_ogni_suono_ha_un_evento():
    with open(SORGENTE, encoding="utf-8") as f:
        sorgente = f.read()
    # Il codice fuori dalla mappa: ogni evento deve comparirvi almeno una volta, scritto fra virgolette
    inizio = sorgente.index("SUONI = {")
    fine = sorgente.index("\n}\n", inizio)
    codice = sorgente[:inizio] + sorgente[fine:]
    muti = {evento for evento in stellaterm.SUONI if f'"{evento}"' not in codice}
    assert not muti, f"eventi in SUONI che non suonano mai: {muti}"
    # E ogni evento chiesto a suona() o come suono di una risposta deve stare nella mappa
    chiesti = set(re.findall(r'suona\("([a-z_]+)"', codice)) | set(re.findall(r'sound="([a-z_]+)"', codice))
    sconosciuti = chiesti - set(stellaterm.SUONI)
    assert not sconosciuti, f"eventi suonati ma non in SUONI: {sconosciuti}"


def test_elenco_per_acu_maker_aggiornato():
    percorso = os.path.join(os.path.dirname(stellaterm.__file__), elenco_suoni.NOME_ELENCO)
    with open(percorso, encoding="utf-8") as f:
        assert f.read() == elenco_suoni.testo_elenco(), "suoni_stellaterm.txt non e' aggiornato: python elenco_suoni.py"
