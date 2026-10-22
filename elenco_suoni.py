# StellaTerm, l'elenco dei suoni per Acu_Maker: un'azione per riga, con il preset che la fa suonare.
# Autori: Gabriele Battaglia (IZ4APU) & ClaudIA (Claude Opus 5.5, UltraCode).
# 22/10/2026, 0.5.0: nasce con la mappa dei suoni a un preset per evento.

"""Rigenera suoni_stellaterm.txt dalla mappa SUONI di stellaterm.py.

Si lancia dalla cartella del progetto con python elenco_suoni.py, ogni volta
che un suono cambia; tests/test_suoni.py controlla che il file sia aggiornato.
"""

import os

from stellaterm import APP_NAME, SUONI, VERSIONE

NOME_ELENCO = "suoni_stellaterm.txt"


def testo_elenco() -> str:
    righe = [
        f"Suoni di {APP_NAME} {VERSIONE}: per ogni azione il preset di Acu_Collection.json che la fa suonare, da ritoccare con Acu_Maker.",
    ]
    righe.extend(f"{azione} Preset: {preset}." for preset, azione in SUONI.values())
    return "\n".join(righe) + "\n"


def main():
    percorso = os.path.join(os.path.dirname(os.path.abspath(__file__)), NOME_ELENCO)
    with open(percorso, "w", encoding="utf-8") as f:
        f.write(testo_elenco())
    print(f"Scritto {percorso}: {len(SUONI)} suoni.")


if __name__ == "__main__":
    main()
