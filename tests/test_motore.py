# StellaTerm, le prove del motore: il parser VT su flussi sintetici e una sessione cmd.exe vera.
# Autori: Gabriele Battaglia (IZ4APU) & ClaudIA (Claude Opus 5.5, UltraCode).
# 22/10/2026, 0.5.0: nascono con la prima versione del repository.

import os
import time

import pytest

from terminal_engine import PROMPT_RE, TerminalEngine


class PtyFinta:
    """Raccoglie cio' che il motore scrive al ConPTY."""

    def __init__(self):
        self.scritto = []
        self.pid = os.getpid()

    def write(self, testo):
        self.scritto.append(testo)
        return len(testo)

    def read(self, blocking=False):
        return ""

    def isalive(self):
        return True


def motore(**opzioni):
    eng = TerminalEngine(on_buffer_updated=lambda: None, on_output_finished=lambda late: None, **opzioni)
    eng.pty = PtyFinta()
    return eng


def dai(eng, *blocchi):
    for blocco in blocchi:
        eng._process_raw_chunk(blocco, time.monotonic())


def buffer_di(eng):
    return eng.get_text_and_start_pos()[0]


def test_righe_e_a_capo():
    eng = motore()
    dai(eng, "abc\r\ndef")
    assert buffer_di(eng) == "abc\ndef"


def test_ris_azzera_lo_schermo_senza_stampare_la_c():
    eng = motore()
    dai(eng, "abc\x1bc")
    assert buffer_di(eng) == ""


def test_esc_uguale_e_maggiore_non_finiscono_nel_testo():
    eng = motore()
    dai(eng, "\x1b=keypad\x1b>")
    assert buffer_di(eng) == "keypad"


def test_titolo_spezzato_fra_esc_e_barra():
    titoli = []
    eng = motore(on_title_changed=titoli.append)
    dai(eng, "x\x1b]0;Titolo\x1b", "\\y")
    assert buffer_di(eng) == "xy"
    assert titoli == ["Titolo"]


def test_titolo_spezzato_a_meta():
    titoli = []
    eng = motore(on_title_changed=titoli.append)
    dai(eng, "a\x1b]0;C:\\WIN", "DOWS\x07b")
    assert buffer_di(eng) == "ab"
    assert titoli == ["C:\\WINDOWS"]


def test_csi_spezzata_fra_due_blocchi():
    eng = motore()
    dai(eng, "abc\x1b[1", ";1Hz")
    assert buffer_di(eng) == "zbc"


def test_tabulazione_sposta_senza_cancellare():
    eng = motore()
    dai(eng, "abcdefghij\r\tX")
    assert buffer_di(eng) == "abcdefghXj"


def test_ultima_colonna_poi_cancellazione_a_sinistra():
    # Dopo l'ultima colonna il cursore resta li' con l'a capo in sospeso: EL 1 ed ED 1 non escono dalla riga
    eng = motore(cols=10, rows=5)
    dai(eng, "0123456789")
    assert eng._cursor_col == 9
    assert eng._wrap_pending
    dai(eng, "\x1b[1K\x1b[1J")
    assert buffer_di(eng) == ""


def test_ed2_tiene_lo_storico_ed3_lo_cancella():
    eng = motore(cols=20, rows=3)
    dai(eng, "uno\r\ndue\r\ntre\r\nquattro")
    assert eng._scrollback == ["uno"]
    dai(eng, "\x1b[2J")
    assert buffer_di(eng) == "uno\ndue\ntre\nquattro"
    dai(eng, "\x1b[3J")
    assert buffer_di(eng) == ""


def test_cls_di_cmd_svuota_tutto():
    eng = motore(cols=20, rows=3)
    dai(eng, "uno\r\ndue\r\ntre\r\nquattro", "\x1b[H\x1b[2J\x1b[3J\x1b[1;1H")
    assert buffer_di(eng) == ""


def test_ed2_nello_schermo_alternativo_non_tocca_lo_storico():
    eng = motore(cols=20, rows=3)
    dai(eng, "uno\r\ndue\r\ntre\r\nquattro", "\x1b[?1049h", "tui\x1b[2J\x1b[3J")
    assert eng._scrollback == ["uno"]


def test_uscita_dallo_schermo_alternativo_in_ordine():
    eng = motore(cols=30, rows=5)
    dai(eng, "prima\r\nE:\\git>edit\r\n", "\x1b[?1049hschermata TUI", "\x1b[?1049l")
    assert buffer_di(eng) == "prima\nE:\\git>edit\nschermata TUI"


def test_posizione_del_cursore_dopo_il_testo_dello_stesso_blocco():
    eng = motore()
    dai(eng, "abc\x1b[6n")
    assert eng.pty.scritto == ["\x1b[1;4R"]


def test_attributi_del_dispositivo():
    eng = motore()
    dai(eng, "\x1b[c\x1b[>c\x1b[0c")
    assert eng.pty.scritto == ["\x1b[?1;2c", "\x1b[?1;2c"]


def test_caratteri_larghi_occupano_due_celle():
    eng = motore()
    dai(eng, "\u4e2d\u6587X\x1b[1;5HY")
    # Come in ConPTY: due celle per ideogramma, quindi la colonna 5 e' quella dopo i due
    assert buffer_di(eng) == "\u4e2d\u6587Y"


def test_segno_combinante_si_unisce_alla_lettera():
    eng = motore()
    dai(eng, "e\u0301!")
    assert buffer_di(eng) == "e\u0301!"
    assert eng._cursor_col == 2


def test_inserimenti_e_scorrimenti_enormi_non_bloccano():
    eng = motore(cols=20, rows=5)
    inizio = time.monotonic()
    dai(eng, "abc\x1b[1;1H\x1b[99999@\x1b[99999S\x1b[99999T\x1b[99999L\x1b[99999M")
    assert time.monotonic() - inizio < 0.5


@pytest.mark.parametrize(
    "riga",
    ["E:\\git>", "C:\\Users\\Gab>", "(venv) E:\\git>", "PS C:\\Users>", ">>> ", "... ", "In [3]: ", "   ...: ", "(Pdb) ", "sqlite> ", "gab@casa:~$ ", "DISKPART> ", "(my env) E:\\git>", "[12:30:01,55] E:\\git>"],
)
def test_prompt_riconosciuti(riga):
    assert PROMPT_RE.match(riga)


@pytest.mark.parametrize("riga", ["45%", "100%", "12>", "OK>", "Scaricamento in corso", "Totale: 3 file"])
def test_non_sono_prompt(riga):
    assert not PROMPT_RE.match(riga)


def test_lo_storico_rispetta_il_tetto():
    eng = motore(cols=20, rows=5, max_lines=60)
    dai(eng, "".join(f"riga {i}\r\n" for i in range(500)))
    righe = buffer_di(eng).split("\n")
    assert len(righe) == 60
    assert righe[-1] == "riga 499"


def test_riga_piu_lunga_dello_schermo_resta_una():
    eng = motore(cols=20, rows=5)
    lunga = "abcdefghij" * 5 + " fine"
    dai(eng, lunga + "\r\nsotto")
    assert buffer_di(eng) == f"{lunga}\nsotto"


def test_riga_lunga_che_esce_dallo_schermo_resta_una():
    eng = motore(cols=20, rows=3)
    lunga = "0123456789" * 5
    dai(eng, lunga + "\r\n" + "".join(f"riga {i}\r\n" for i in range(6)))
    assert buffer_di(eng).split("\n")[0] == lunga


def test_spazio_sull_ultima_colonna_non_si_perde():
    eng = motore(cols=10, rows=5)
    dai(eng, "123456789 abc")
    assert buffer_di(eng) == "123456789 abc"


def test_cancellazione_a_destra_con_a_capo_in_sospeso():
    # Come in xterm: dopo l'ultima colonna EL 0 cancella quella colonna, e il carattere dopo resta sulla riga
    eng = motore(cols=10, rows=5)
    dai(eng, "0123456789\x1b[KZ")
    assert buffer_di(eng) == "012345678Z"


def test_righe_di_output_che_sembrano_prompt_non_si_fondono():
    eng = motore(cols=40, rows=4)
    dai(eng, "100%\r\n100% completato\r\nC#\r\nC# e' il linguaggio\r\nE:\\git>\r\nE:\\git>dir\r\n")
    righe = buffer_di(eng).split("\n")
    assert righe[:4] == ["100%", "100% completato", "C#", "C# e' il linguaggio"]
    assert righe[4] == "E:\\git>dir"


def test_prefissi_privati_ignorati():
    eng = motore(cols=20, rows=5)
    dai(eng, "riga 1\r\nE:\\git>", "\x1b[>1u\x1b[>2T", "dir")
    assert buffer_di(eng) == "riga 1\nE:\\git>dir"


def test_canc_e_controlli_c1_non_si_stampano():
    eng = motore()
    dai(eng, "ab\x7fcd\x85ef\x9bgh")
    assert buffer_di(eng) == "abcdefgh"


def test_comando_remoto_con_lo_spazio_finale():
    eng = motore()
    dai(eng, "In [1]: import ")
    assert eng._get_remote_command() == "import "


def test_comando_remoto_su_piu_righe():
    eng = motore(cols=20, rows=5)
    comando = "python " + "x" * 30
    dai(eng, "E:\\git>" + comando)
    assert eng._get_remote_command() == comando


def test_backspace_veri_per_riscrivere_la_riga():
    eng = motore()
    dai(eng, "E:\\git>cd Mine")
    eng.send_command("dir")
    assert eng.pty.scritto[-1] == "\x7f" * 7 + "dir\r"


def test_risposta_a_un_programma_senza_prompt():
    eng = motore()
    dai(eng, "Nome: ")
    eng.send_command("Gab")
    assert eng.pty.scritto[-1] == "Gab\r"


def test_tab_rifiutato_dove_entrerebbe_come_carattere():
    eng = motore()
    dai(eng, ">>> ")
    assert not eng.request_tab_completion("pri")
    eng2 = motore()
    dai(eng2, "Nome: ")
    assert not eng2.request_tab_completion("Gab")
    eng3 = motore()
    dai(eng3, "E:\\git>")
    assert eng3.request_tab_completion("cd mi")
    assert eng3.pty.scritto[-1] == "cd mi\t"


def test_lettura_dalla_riga_riscritta_dal_programma():
    # Un prompt alla maniera di Gabriele, fra due ritorni carrello, con il cursore a inizio riga
    eng = motore()
    dai(eng, "Menu\r\n\rScegli una voce\r")
    eng.send_raw_key("1")
    dai(eng, "Hai scelto Uno.\r\nPremi un tasto")
    testo_buffer, inizio = eng.get_text_and_start_pos()
    assert testo_buffer[inizio:].startswith("Hai scelto Uno.")


def test_lettura_dopo_l_eco_del_comando():
    eng = motore()
    dai(eng, "prima\r\nE:\\git>")
    eng.send_command("dir")
    dai(eng, "dir\r\n file1.txt\r\n\r\nE:\\git>")
    testo_buffer, inizio = eng.get_text_and_start_pos()
    assert testo_buffer[inizio:].startswith(" file1.txt")


def test_ctrl_c_al_prompt_fermo_non_arma_il_salto():
    eng = motore()
    dai(eng, "E:\\git>")
    eng._startup_phase = False
    eng.send_interrupt()
    assert eng.pty.scritto[-1] == "\x03"
    assert not eng._waiting_for_output_end


winpty = pytest.importorskip("winpty")


def aspetta(condizione, secondi=10.0):
    fine = time.monotonic() + secondi
    while time.monotonic() < fine:
        if condizione():
            return True
        time.sleep(0.02)
    return False


def test_sessione_cmd_vera(tmp_path):
    finiti = []
    eng = TerminalEngine(on_buffer_updated=lambda: None, on_output_finished=lambda late: finiti.append(late), cwd=str(tmp_path))
    eng.start()
    try:
        assert aspetta(lambda: eng.is_at_shell_prompt()), "il prompt di cmd non arriva"
        assert eng.get_current_prompt_display().lower() == f"{str(tmp_path).lower()}>"
        eng.send_command("echo ciao StellaTerm")
        assert aspetta(lambda: finiti), "la fine dell'output non viene riconosciuta"
        assert finiti == [False]
        testo_buffer, inizio = eng.get_text_and_start_pos()
        assert testo_buffer[inizio:].startswith("ciao StellaTerm")
        assert eng.is_at_shell_prompt()
    finally:
        eng.stop()
    assert not eng.is_alive()


def test_ping_finisce_solo_alla_fine(tmp_path):
    # Fra una risposta e l'altra ping dorme: non e' la fine del comando
    finiti = []
    eng = TerminalEngine(on_buffer_updated=lambda: None, on_output_finished=lambda late: finiti.append((time.monotonic(), late)), cwd=str(tmp_path))
    eng.start()
    try:
        assert aspetta(lambda: eng.is_at_shell_prompt())
        inizio = time.monotonic()
        eng.send_command("ping -n 3 127.0.0.1")
        assert aspetta(lambda: finiti, 15.0)
        assert finiti[0][0] - inizio > 1.8
        assert finiti[0][1] is False
        assert eng.is_at_shell_prompt()
    finally:
        eng.stop()


def test_tab_vero_poi_un_altro_comando(tmp_path):
    (tmp_path / "Mine").mkdir()
    completati = []
    finiti = []
    eng = TerminalEngine(
        on_buffer_updated=lambda: None,
        on_output_finished=lambda late: finiti.append(late),
        on_tab_completed=lambda nuovo, sotto, vecchio, vecchi_sotto: completati.append(nuovo),
        cwd=str(tmp_path),
    )
    eng.start()
    try:
        assert aspetta(lambda: eng.is_at_shell_prompt())
        assert eng.request_tab_completion("cd mi")
        assert aspetta(lambda: completati)
        assert completati[0].lower() == "cd mine"
        # La riga di cmd contiene il completamento: il comando nuovo la deve sostituire per intero
        eng.send_command("echo sostituito")
        assert aspetta(lambda: finiti)
        testo_buffer, inizio = eng.get_text_and_start_pos()
        assert testo_buffer[inizio:].startswith("sostituito")
    finally:
        eng.stop()


def test_input_di_python_poi_la_fine_vera(tmp_path):
    finiti = []
    eng = TerminalEngine(on_buffer_updated=lambda: None, on_output_finished=lambda late: finiti.append(late), cwd=str(tmp_path))
    eng.start()
    try:
        assert aspetta(lambda: eng.is_at_shell_prompt())
        eng.send_command('python -B -c "print(input(\'Nome: \').upper())"')
        assert aspetta(lambda: finiti, 15.0), "l'attesa di input() non viene riconosciuta"
        assert not eng.is_at_shell_prompt()
        eng.send_command("gab")
        assert aspetta(lambda: len(finiti) == 2, 15.0)
        testo_buffer, inizio = eng.get_text_and_start_pos()
        assert testo_buffer[inizio:].startswith("GAB")
        assert eng.is_at_shell_prompt()
    finally:
        eng.stop()
