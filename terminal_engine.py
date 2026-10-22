"""
Motore terminale ConPTY con Schermo Virtuale 2D per StellaTerm.
Autori: Gabriele Battaglia (IZ4APU), Stella (Gemini 4 Argon) & ClaudIA (Claude Opus 5.5, UltraCode).

Gestisce:
- Pseudo-console di Windows (tramite pywinpty) su una griglia virtuale 2D
  (righe x colonne) dotata di buffer di scrollback configurabile (predefinito 5000 righe).
- Interpretazione delle sequenze VT100/ANSI/xterm di posizionamento
  bidimensionale del cursore (CUP, CUU, CUD, CUF, CUB, CNL, CPL, CHA, VPA,
  DECSC/DECRC \x1b7/\x1b8, ED, EL, ECH, DCH, ICH, IL, DL, SU, SD, DECSTBM),
  dello schermo alternativo e delle domande al terminale (DA, DSR).
  Il ConPTY che accompagna pywinpty inoltra cosi' come sono le sequenze dei
  programmi, e lascia al terminale le risposte alle loro domande.
- Separazione delle 3 zone rispetto a `cursor_row`:
  1. Storico + Output (scrollback + righe sopra il cursore)
  2. Riga attiva del prompt (`cursor_row`), per estrarre l'indirizzo/prompt
     corrente e sincronizzare l'autocompletamento con il campo di input
  3. Area popup/suggerimenti sotto `cursor_row` (es. menu di IPython / prompt_toolkit)
- Eliminazione totale delle righe vuote per una lettura continua con NVDA/JAWS,
  con le righe spezzate dall'a capo automatico ricomposte in una sola.
"""

import contextlib
import ctypes
import os
import re
import threading
import time
import unicodedata
from ctypes import wintypes
from typing import NamedTuple

try:
    from winpty import PTY, WinptyError
except ImportError:
    # Senza pywinpty il motore si importa lo stesso, e start() lo dice con una frase
    PTY = None

    class WinptyError(Exception):
        """Segnaposto dell'eccezione di pywinpty, quando la libreria manca."""


class StartError(Exception):
    """La sessione ConPTY non parte: il messaggio e' una frase da mostrare all'utente."""


# Limite predefinito del buffer scorrevole (in righe)
MAX_BUFFER_LINES = 5000

# Dimensioni della griglia virtuale 2D e della sessione ConPTY
SCREEN_COLS = 200
SCREEN_ROWS = 50

# Intervallo minimo, in secondi, fra due misure della CPU dell'albero dei processi:
# su 250 ms un programma al lavoro accumula sempre piu' scatti da 15,6 ms
CPU_SAMPLE_INTERVAL = 0.25

# Lo storico si pota quando supera il tetto di queste righe, e non a ogni riga che
# scorre: la vista che si mostra resta comunque entro il tetto esatto
PRUNE_SLACK = 256

# Silenzio dopo il quale un programma senza CPU si considera in attesa: breve se sulla
# riga del cursore c'e' una domanda (input, dgt, una pausa), lungo se la riga e' vuota,
# come fra due risposte di ping o in un comando che aspetta la rete
WAIT_SILENCE_WITH_TEXT = 0.3
WAIT_SILENCE_EMPTY_ROW = 2.0

# Il giro del Tab: se la riga resta uguale all'eco si aspetta al piu' TAB_WAIT secondi
# il completamento, e in ogni caso il giro si chiude dopo TAB_LIMIT secondi
TAB_WAIT = 0.6
TAB_LIMIT = 1.5

# Prefissi facoltativi di un prompt di shell: un ambiente virtuale fra parentesi, anche
# con spazi nel nome, e un'ora fra parentesi quadre di un prompt di cmd personalizzato
_PROMPT_HEAD = r"(?:\([^()\r\n]+\)\s+)?(?:\[[^\]\r\n]*\]\s*)?"
# cmd.exe con il percorso, e PowerShell con il percorso o con un'altra posizione
_CMD_PROMPT = _PROMPT_HEAD + r"[A-Za-z]:\\[^>\r\n]*>"
_PS_PROMPT = _PROMPT_HEAD + r"PS [^>\r\n]*> ?"
# Gli interpreti: Python, IPython, pdb, le continuazioni di PowerShell e di cmd
_INTERP_PROMPT = r">>> ?|\.\.\. ?|\s*\.{3,}: ?|In \[\d+\]: ?|\(Pdb\+{0,2}\) ?|>> ?|(?:Altro|More)\? ?"
# I prompt generici (sqlite, diskpart, bash via ssh): una parola con almeno una lettera,
# seguita da > # $ o % e da uno spazio, oppure il solo $ di Git Bash. Valgono solo con il
# cursore dopo di loro: '45%' o un menu che riporta il cursore a inizio riga non sono prompt
_GENERIC_PROMPT = _PROMPT_HEAD + r"(?:(?=[^>#$%\s]*[A-Za-z])[\w.\-@:~/]+[>#$%] |\$ )"

# Una riga che contiene soltanto un prompt
PROMPT_RE = re.compile(rf"^(?:{_CMD_PROMPT}|{_PS_PROMPT}|{_INTERP_PROMPT}|{_GENERIC_PROMPT})\s*$")
# Un prompt di shell o di interprete da solo: il solo che si fonde con il suo ridisegno
STRONG_PROMPT_RE = re.compile(rf"^(?:{_CMD_PROMPT}|{_PS_PROMPT}|{_INTERP_PROMPT})\s*$")
# Prompt e testo scritto dopo di lui sulla stessa riga
STRONG_SPLIT_RE = re.compile(rf"^({_CMD_PROMPT}|{_PS_PROMPT}|{_INTERP_PROMPT})(.*)$", re.DOTALL)
GENERIC_SPLIT_RE = re.compile(rf"^({_GENERIC_PROMPT})(.*)$", re.DOTALL)
# Il prefisso di un prompt di cmd.exe o di PowerShell
SHELL_PROMPT_RE = re.compile(rf"^(?:{_CMD_PROMPT}|{_PS_PROMPT})$")
# Interpreti in cui il Tab non completa ma entra nella riga come carattere: il REPL base
# di Python, pdb senza readline e la continuazione di cmd
NO_TAB_PROMPT_RE = re.compile(r"^\s*(?:>>>|\.\.\.|\(Pdb\+{0,2}\)|Altro\?|More\?)\s*$")

# Riconoscimento di un prompt che contiene un percorso disco (es. E:\dati\git> o PS E:\dati\git>)
PATH_PROMPT_RE = re.compile(rf"^{_PROMPT_HEAD}(?:PS\s+)?([A-Za-z]:\\[^>]*>)")

# Prefisso numerato di IPython (es. 'In [2]:') per unificare eventuali ridisegni della stessa riga
IPYTHON_IN_RE = re.compile(r"^(In \[\d+\]:)")

# Cambio del titolo della finestra (OSC 0 o 2), chiuso da BEL o da ESC barra rovesciata
OSC_TITLE_RE = re.compile(r"\x1b\](?:0|2);([^\x07\x1b]*)(?:\x07|\x1b\\)")
# Tutte le stringhe OSC, e quelle DCS, PM e APC, che non disegnano nulla
OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
DCS_RE = re.compile(r"\x1b[P^_].*?(?:\x07|\x1b\\)", re.DOTALL)
# Una stringa di controllo aperta e non ancora chiusa in fondo al blocco letto,
# anche quando il blocco finisce fra l'ESC e la barra rovesciata del terminatore
OPEN_STRING_RE = re.compile(r"\x1b[\]P^_](?:[^\x07\x1b]|\x1b(?!\\))*\Z", re.DOTALL)
# Sequenze CSI (ESC [ ...) e sequenze ESC a due o piu' byte: con intermedi
# (ESC ( B), oppure un solo carattere finale fra 0 e la tilde (ESC 7, ESC c, ESC =)
TOKEN_RE = re.compile(r"\x1b\[([0-9:;?<=>!]*)([ -/]*[@-~])|\x1b([ -/]+[0-~]|[0-~])")
# Il testo fra le sequenze, a pezzi: le corse di caratteri semplici, stretti e non
# combinanti, si scrivono nella griglia a fette; tutto il resto un carattere alla volta
_TEXT_RUN_RE = re.compile(r"[\x20-\x7e\xa0-\u02ff]+|.", re.DOTALL)
_SIMPLE_CHAR_RE = re.compile(r"[\x20-\x7e\xa0-\u02ff]")


def split_prompt_and_command(text: str) -> tuple[str, str] | None:
    """
    Se `text` inizia con un prompt di shell o di interprete riconosciuto, restituisce
    (prefisso_prompt, testo_comando). Altrimenti restituisce None.
    """
    m = STRONG_SPLIT_RE.match(text)
    if m:
        return m.group(1), m.group(2)
    return None


def split_prompt_at_cursor(text: str) -> tuple[str, str] | None:
    """
    Come split_prompt_and_command, ma per il testo della riga del cursore fino al
    cursore: qui valgono anche i prompt generici, perche' il cursore aspetta subito
    dopo di loro o dopo il comando scritto.
    """
    split = split_prompt_and_command(text)
    if split is not None:
        return split
    m = GENERIC_SPLIT_RE.match(text)
    if m:
        return m.group(1), m.group(2)
    return None


def redraws_previous_line(prev: str, line: str) -> bool:
    """
    Vero se `line` e' il ridisegno della riga `prev` e deve prenderne il posto invece
    di seguirla: lo stesso In [n]: di IPython, oppure un prompt nudo di shell o di
    interprete seguito dal comando scritto o completato sulla stessa riga. I prompt
    generici non si fondono: '100%' seguito da '100% completato' sono due righe vere.
    """
    m_prev = IPYTHON_IN_RE.match(prev)
    m_curr = IPYTHON_IN_RE.match(line)
    if m_prev and m_curr and m_prev.group(1) == m_curr.group(1):
        return True
    return bool(STRONG_PROMPT_RE.match(prev)) and line.startswith(prev.rstrip())


def clean_command_text(text: str) -> str:
    """
    Il testo di un comando senza caratteri di controllo: un Tab arriverebbe alla shell
    come completamento e un Esc svuoterebbe la riga di cmd, quindi i Tab diventano
    spazi e gli altri caratteri sotto lo spazio si tolgono.
    """
    text = text.replace("\t", " ")
    return "".join(ch for ch in text if ch >= " " and ch != "\x7f")


class Snapshot(NamedTuple):
    """Le righe da mostrare, con cio' che serve alla finestra per aggiornarle a pezzi."""

    lines: list[str]
    # Posizione assoluta della prima riga, contando quelle gia' uscite dal tetto
    first_line: int
    # Cambia quando lo storico si svuota (cls, Ctrl+L): allora si riscrive tutto
    epoch: int
    # Indice, in lines, della riga da cui comincia la lettura
    start_line: int


class _Row(list):
    """Le celle di una riga della griglia, e se la riga continua nella successiva
    per l'a capo automatico."""

    __slots__ = ("wrapped",)

    def __init__(self, cells):
        super().__init__(cells)
        self.wrapped = False


# --- Ispezione leggera dell'albero dei processi Windows (ctypes) ---

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_kernel32.Process32FirstW.restype = wintypes.BOOL
_kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_kernel32.Process32NextW.restype = wintypes.BOOL
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.GetProcessTimes.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(FILETIME),
    ctypes.POINTER(FILETIME),
    ctypes.POINTER(FILETIME),
    ctypes.POINTER(FILETIME),
]
_kernel32.GetProcessTimes.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


def _filetime_to_int(ft: FILETIME) -> int:
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


def get_process_tree_info(root_pid: int) -> tuple[list[tuple[int, str]], int]:
    """
    Restituisce (lista_di_(pid, nome_exe), tempo_cpu_totale_100ns)
    per il processo root_pid e tutti i suoi discendenti.
    """
    if not root_pid:
        return [], 0

    snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    invalid = wintypes.HANDLE(-1).value
    if not snap or snap == invalid:
        return [], 0

    children_map: dict[int, list[tuple[int, str]]] = {}
    root_name = "cmd.exe"
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if _kernel32.Process32FirstW(snap, ctypes.byref(pe)):
            while True:
                pid = pe.th32ProcessID
                ppid = pe.th32ParentProcessID
                # Il nome si porta in minuscolo solo per i processi dell'albero, piu' sotto
                name = pe.szExeFile
                if pid == root_pid:
                    root_name = name
                else:
                    children_map.setdefault(ppid, []).append((pid, name))
                if not _kernel32.Process32NextW(snap, ctypes.byref(pe)):
                    break
    finally:
        _kernel32.CloseHandle(snap)

    tree: list[tuple[int, str]] = [(root_pid, root_name.lower())]
    queue = [root_pid]
    visited = {root_pid}
    while queue:
        curr = queue.pop(0)
        for child_pid, child_name in children_map.get(curr, []):
            if child_pid not in visited:
                visited.add(child_pid)
                tree.append((child_pid, child_name.lower()))
                queue.append(child_pid)

    total_cpu = 0
    creation = FILETIME()
    exit_t = FILETIME()
    kernel_t = FILETIME()
    user_t = FILETIME()
    for pid, _ in tree:
        hproc = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if hproc:
            try:
                if _kernel32.GetProcessTimes(
                    hproc,
                    ctypes.byref(creation),
                    ctypes.byref(exit_t),
                    ctypes.byref(kernel_t),
                    ctypes.byref(user_t),
                ):
                    total_cpu += _filetime_to_int(kernel_t) + _filetime_to_int(user_t)
            finally:
                _kernel32.CloseHandle(hproc)

    return tree, total_cpu


class TerminalEngine:
    """
    Gestisce una sessione interattiva ConPTY su uno schermo virtuale 2D
    con buffer di scrollback configurabile e supporto nativo all'autocompletamento.
    """

    def __init__(
        self,
        on_buffer_updated,
        on_output_finished,
        on_title_changed=None,
        on_process_exited=None,
        on_prompt_changed=None,
        on_tab_completed=None,
        on_error=None,
        max_lines: int = MAX_BUFFER_LINES,
        cwd: str | None = None,
        cols: int = SCREEN_COLS,
        rows: int = SCREEN_ROWS,
    ):
        self.on_buffer_updated = on_buffer_updated
        # Chiamata con late=True quando il prompt torna dopo che il programma era gia'
        # stato creduto in attesa: e' la fine vera, arrivata dopo una fine apparente
        self.on_output_finished = on_output_finished
        self.on_title_changed = on_title_changed
        self.on_process_exited = on_process_exited
        self.on_prompt_changed = on_prompt_changed
        self.on_tab_completed = on_tab_completed
        # Chiamata dal thread di lettura con l'eccezione, quando un giro va storto
        self.on_error = on_error
        self.max_lines = max(50, int(max_lines))
        self.initial_cwd = cwd or os.getcwd()
        self.cols = cols
        self.rows = rows

        self.pty: PTY | None = None
        self._lock = threading.RLock()
        self._stop_event = threading.Event()

        # Griglia virtuale 2D + buffer di scrollback (solo righe non vuote)
        self._scrollback: list[str] = []
        # Vero quando l'ultima riga dello storico continua nella prima della griglia
        self._scrollback_open: bool = False
        # Righe uscite dalla testa dello storico per il tetto, e epoca dello storico,
        # che sale quando lo si svuota: vedi get_snapshot
        self._dropped_lines: int = 0
        self._epoch: int = 0
        self._grid: list[_Row] = self._make_empty_grid()
        self._cursor_row: int = 0
        # Sempre fra 0 e cols - 1: dopo un carattere scritto nell'ultima colonna il
        # cursore resta li' e _wrap_pending dice che il prossimo va a capo
        self._cursor_col: int = 0
        self._wrap_pending: bool = False
        self._autowrap: bool = True
        self._saved_cursor: tuple[int, int] = (0, 0)
        self._scroll_top: int = 0
        self._scroll_bottom: int = self.rows - 1
        self._last_printed_char: str = " "

        # Salvataggio per eventuale Alternate Screen Buffer (\x1b[?1049h)
        self._alt_screen_active: bool = False
        self._saved_primary_grid: list[_Row] | None = None
        self._saved_primary_cursor: tuple[int, int] = (0, 0)

        self._raw_tail: str = ""
        # Risposte alle domande del programma (DA, DSR) e titoli nuovi, raccolti sotto il
        # lucchetto mentre si analizza un blocco e consegnati appena lo si rilascia
        self._pending_replies: list[str] = []
        self._pending_titles: list[str] = []

        # Indirizzo / prompt corrente rilevato sulla riga del cursore
        self._last_path_prompt: str = f"{self.initial_cwd.rstrip('\\/')}>"
        self._current_prompt_display: str = self._last_path_prompt

        # Tracciamento inizio output dell'ultimo comando. L'indice e' quello della riga
        # del cursore al momento dell'invio; se quella riga e' ancora il prompt con
        # l'eco del comando, la lettura parte dalla successiva (vedi _start_line_index)
        self._cmd_start_line_idx: int = 0
        self._cmd_anchor_text: str | None = None
        self._cmd_sent_text: str = ""
        self._awaiting_first_newline_after_cmd: bool = False
        self._data_received_since_send: bool = False
        self._waiting_for_output_end: bool = False
        # Vero dopo una fine apparente: il programma taceva senza CPU ed e' stato creduto
        # in attesa, ma il rilevamento resta armato per il prompt che tornera'
        self._apparent_end_signalled: bool = False
        self._startup_phase: bool = True

        # Stato per il round-trip dell'autocompletamento (tasto Tab)
        self._tab_pending: bool = False
        self._tab_request_time: float = 0.0
        self._tab_data_received: bool = False
        self._tab_bell: bool = False
        self._tab_old_cmd: str = ""
        self._tab_old_below: list[str] = []

        # Timestamp e stato CPU per il rilevamento di fine comando
        self._last_data_time: float = 0.0
        self._cmd_sent_time: float = 0.0
        # None finche' il thread di lettura non ha preso la misura di partenza
        self._last_cpu_total: int | None = None
        self._last_cpu_check_time: float = 0.0
        self._last_tree: list[tuple[int, str]] | None = None
        # Sale a ogni comando, tasto, interruzione o Tab mandati alla sessione: una
        # decisione di fine presa nel frattempo riguarda il comando di prima e si scarta
        self._generation: int = 0
        self._window_title: str = "Prompt dei comandi"

        self._reader_thread: threading.Thread | None = None

    # --- Griglia e righe ---

    def _new_row(self) -> _Row:
        return _Row([" "] * self.cols)

    def _make_empty_grid(self) -> list[_Row]:
        return [self._new_row() for _ in range(self.rows)]

    def _get_row_text(self, row: int) -> str:
        if 0 <= row < self.rows:
            return "".join(self._grid[row]).rstrip()
        return ""

    def _row_text_for_join(self, row: int) -> str:
        """Il testo di una riga per lo storico: intero se continua nella successiva, cosi'
        uno spazio caduto proprio sull'ultima colonna non va perso, altrimenti senza spazi
        in coda."""
        cells = self._grid[row]
        return "".join(cells) if cells.wrapped else "".join(cells).rstrip()

    def _grid_lines(self, last_row: int) -> list[str]:
        """Le righe logiche della griglia fino a last_row compresa: quelle spezzate
        dall'a capo automatico diventano una riga sola."""
        out: list[str] = []
        pieces: list[str] = []
        for r in range(last_row + 1):
            cells = self._grid[r]
            if cells.wrapped and r < last_row:
                pieces.append("".join(cells))
                continue
            pieces.append("".join(cells).rstrip())
            out.append("".join(pieces))
            pieces = []
        return out

    def _cursor_line_start(self) -> int:
        """La riga della griglia in cui comincia la riga logica del cursore."""
        start = self._cursor_row
        while start > 0 and self._grid[start - 1].wrapped:
            start -= 1
        return start

    def _cursor_line_up_to_cursor(self) -> str:
        """Il testo della riga logica del cursore, dall'inizio fino al cursore, senza
        togliere gli spazi: la shell li ha nella sua riga, e contano per l'eco."""
        start = self._cursor_line_start()
        parts = ["".join(self._grid[r]) for r in range(start, self._cursor_row)]
        end = self.cols if self._wrap_pending else self._cursor_col
        parts.append("".join(self._grid[self._cursor_row][:end]))
        text = "".join(parts)
        if start == 0 and self._scrollback_open and self._scrollback:
            text = self._scrollback[-1] + text
        return text

    def _cursor_line_text(self) -> str:
        """Il testo della riga logica del cursore fino alla riga del cursore compresa,
        senza spazi in coda."""
        start = self._cursor_line_start()
        pieces = ["".join(self._grid[r]) for r in range(start, self._cursor_row)]
        pieces.append("".join(self._grid[self._cursor_row]).rstrip())
        text = "".join(pieces)
        if start == 0 and self._scrollback_open and self._scrollback:
            text = self._scrollback[-1] + text
        return text

    def _lines_before_cursor_line(self) -> int:
        """Quante righe della vista precedono la riga logica del cursore."""
        start = self._cursor_line_start()
        if start == 0:
            return len(self._scrollback) - (1 if self._scrollback_open and self._scrollback else 0)
        return len(self._collect_deduplicated_lines(start - 1))

    def _get_lines_below_cursor(self) -> list[str]:
        """Restituisce le righe non vuote situate sotto `cursor_row` (es. menu suggerimenti)."""
        out: list[str] = []
        for r in range(self._cursor_row + 1, self.rows):
            txt = self._get_row_text(r)
            if txt.strip():
                out.append(txt)
        return out

    # --- Avvio e chiusura ---

    def start(self, shell_cmd: str | None = None):
        """
        Avvia la sessione ConPTY e il thread di lettura/monitoraggio. Solleva
        StartError, con una frase da mostrare all'utente, se la sessione non parte.
        """
        if PTY is None:
            raise StartError(
                "StellaTerm non trova pywinpty, la libreria che apre la console di Windows: si installa con python -m pip install pywinpty."
            )
        if shell_cmd is None:
            shell_cmd = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")

        # Le variabili per i programmi lanciati nel terminale si mettono nell'ambiente di
        # StellaTerm, che cmd eredita con i nomi e l'ordine originali
        os.environ.update(
            {"PYTHON_BASIC_REPL": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "NO_COLOR": "1"}
        )

        try:
            pty = PTY(self.cols, self.rows)
            started = pty.spawn(shell_cmd, cwd=self.initial_cwd)
        except WinptyError as exc:
            raise StartError(f"La console non parte con {shell_cmd} nella cartella {self.initial_cwd}: {exc}") from exc
        if not started:
            raise StartError(f"La console non parte con {shell_cmd} nella cartella {self.initial_cwd}.")
        self.pty = pty

        self._stop_event.clear()
        self._reader_thread = threading.Thread(
            target=self._loop, name="StellaTerm-ConPTY", daemon=True
        )
        self._reader_thread.start()

    def is_alive(self) -> bool:
        pty = self.pty
        try:
            return pty is not None and pty.isalive()
        except WinptyError:
            return False

    def stop(self):
        """
        Ferma il thread di lettura e chiude la sessione ConPTY. Non scrive exit: se in
        cmd gira un programma, la parola arriverebbe a lui; e' la chiusura della pseudo
        console, quando la PTY non ha piu' riferimenti, a chiudere cmd e i suoi figli.
        """
        self._stop_event.set()
        self.pty = None
        reader = self._reader_thread
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.0)

    def running_programs(self) -> list[str]:
        """
        I nomi dei programmi che girano nella sessione oltre alla shell, per esempio
        ['python.exe']: vuoto quando cmd e' fermo al suo prompt. Fotografa i processi,
        quindi si chiama di rado, per esempio alla chiusura della finestra.
        """
        pty = self.pty
        if pty is None:
            return []
        tree, _cpu = get_process_tree_info(pty.pid)
        return [name for _pid, name in tree[1:] if name not in ("conhost.exe", "openconsole.exe")]

    # --- Storico ---

    def set_max_lines(self, max_lines: int):
        """Aggiorna dinamicamente la capienza massima del buffer di righe."""
        with self._lock:
            self.max_lines = max(50, int(max_lines))
            self._enforce_max_lines(exact=True)
        if self.on_buffer_updated:
            self.on_buffer_updated()

    def _enforce_max_lines(self, exact: bool = False):
        """
        Mantiene il numero totale di righe entro `self.max_lines`. Senza exact si
        pota solo oltre PRUNE_SLACK righe di troppo, e allora in un colpo solo.
        """
        # Via rapida: con lo storico e tutta la griglia sotto il tetto non c'e' niente da
        # togliere, e si evita di ricomporre le righe della griglia a ogni blocco
        slack = 0 if exact else PRUNE_SLACK
        if len(self._scrollback) + self.rows <= self.max_lines + slack:
            return
        grid_non_empty = sum(
            1 for r in range(self.rows) if self._get_row_text(r).strip()
        )
        allowed_scrollback = max(0, self.max_lines - grid_non_empty)
        if len(self._scrollback) > allowed_scrollback:
            overflow = len(self._scrollback) - allowed_scrollback
            del self._scrollback[:overflow]
            self._cmd_start_line_idx = max(0, self._cmd_start_line_idx - overflow)
            self._dropped_lines += overflow
            if not self._scrollback:
                self._scrollback_open = False

    def _push_to_scrollback(self, line: str, wrapped: bool = False):
        """
        Accoda una riga allo storico. Se l'ultima riga dello storico continuava per l'a
        capo automatico, questa la completa. Se e' il ridisegno della riga di prompt
        appena accodata (lo stesso In [n]: di IPython, o lo stesso prompt con il
        comando completato) prende il suo posto invece di ripeterla.
        """
        if self._scrollback_open and self._scrollback:
            joined = self._scrollback[-1] + line
            self._scrollback[-1] = joined if wrapped else joined.rstrip()
            self._scrollback_open = wrapped
            return
        self._scrollback_open = False
        if not line.strip():
            return
        if not wrapped and self._scrollback and redraws_previous_line(self._scrollback[-1], line):
            self._scrollback[-1] = line
            return
        self._scrollback.append(line)
        self._scrollback_open = wrapped

    def _push_grid_rows(self, last_row: int):
        """Passa nello storico le righe della griglia da 0 a last_row compresa, e le svuota."""
        for r in range(last_row + 1):
            self._push_to_scrollback(self._row_text_for_join(r), self._grid[r].wrapped)
            self._grid[r] = self._new_row()

    def _collect_deduplicated_lines(self, max_row: int | None = None) -> list[str]:
        """
        Estrae tutte le righe non vuote dallo scrollback e dalla griglia 2D
        (fino a `max_row` inclusa, o fino in fondo se `max_row` è None; -1 vuol dire
        il solo storico), evitando duplicazioni di prompt nudi consecutivi (es.
        ridisegni di IPython) e ricomponendo le righe spezzate dall'a capo automatico.
        """
        limit_row = (
            self.rows - 1 if max_row is None else max(-1, min(max_row, self.rows - 1))
        )
        lines = list(self._scrollback)
        grid_lines = self._grid_lines(limit_row) if limit_row >= 0 else []
        if grid_lines and self._scrollback_open and lines:
            lines[-1] = (lines[-1] + grid_lines.pop(0)).rstrip()
        elif self._scrollback_open and lines:
            lines[-1] = lines[-1].rstrip()
        for txt in grid_lines:
            if not txt.strip():
                continue
            if lines and redraws_previous_line(lines[-1], txt):
                lines[-1] = txt
                continue
            lines.append(txt)
        return lines

    def _start_line_index(self, lines: list[str]) -> int:
        """
        L'indice, nelle righe della vista non ancora tagliate, della riga da cui leggere
        la risposta all'ultimo comando. Se la riga del cursore al momento dell'invio
        c'e' ancora con il suo testo (il prompt, poi l'eco), la risposta comincia dopo di
        lei; se e' stata riscritta, per esempio da un programma che riporta il cursore a
        inizio riga, la risposta comincia proprio li'.
        """
        idx = self._cmd_start_line_idx
        anchor = self._cmd_anchor_text
        if anchor is None or idx >= len(lines):
            return idx
        line = lines[idx]
        if anchor.strip():
            if line.startswith(anchor.rstrip()):
                return idx + 1
        elif self._cmd_sent_text.strip() and line.strip() == self._cmd_sent_text.strip():
            # Riga vuota al momento dell'invio, e ora c'e' l'eco di cio' che si e' scritto
            return idx + 1
        return idx

    def get_snapshot(self, line_idx: int | None = None) -> Snapshot:
        """
        Le righe da mostrare (senza righe vuote e dentro il tetto max_lines), la
        posizione assoluta della prima, l'epoca dello storico e la riga da cui
        leggere: quella indicata da line_idx, se data, altrimenti l'inizio della
        risposta all'ultimo comando. La posizione assoluta conta anche le righe
        gia' uscite dal tetto, e l'epoca cambia quando lo storico si svuota: con
        le due la finestra aggiorna soltanto le righe cambiate.
        """
        with self._lock:
            lines = self._collect_deduplicated_lines(None)
            idx = self._start_line_index(lines) if line_idx is None else line_idx
            view_trim = max(0, len(lines) - self.max_lines)
            if view_trim:
                lines = lines[view_trim:]
            start_line = max(0, min(idx - view_trim, len(lines) - 1)) if lines else 0
            return Snapshot(lines, self._dropped_lines + view_trim, self._epoch, start_line)

    def get_text_and_start_pos(self) -> tuple[str, int]:
        """
        Restituisce l'intero testo del buffer (senza alcuna riga vuota) e
        l'indice in caratteri dell'inizio dell'output dell'ultimo comando.
        """
        snap = self.get_snapshot()
        full_text = "\n".join(snap.lines)
        start_pos = sum(len(line) + 1 for line in snap.lines[: snap.start_line])
        return full_text, min(start_pos, len(full_text))

    def clear_buffer(self):
        """Svuota il buffer delle righe completate, preservando la riga corrente del cursore."""
        with self._lock:
            self._scrollback.clear()
            self._scrollback_open = False
            for r in range(self.rows):
                if r != self._cursor_row:
                    self._grid[r] = self._new_row()
            self._cmd_start_line_idx = 0
            self._cmd_anchor_text = None
            self._epoch += 1
        if self.on_buffer_updated:
            self.on_buffer_updated()

    def append_internal_output(self, cmd_echo: str, output_lines: list[str]) -> int:
        """
        Inserisce nel buffer un comando interno (@st ...) e le relative righe di
        risposta senza alterare la posizione del cursore ConPTY nella griglia 2D.
        Restituisce l'indice della prima riga della risposta, da passare a get_snapshot.
        """
        with self._lock:
            # Al prompt fermo le righe sopra il cursore passano nello storico, cosi' la
            # risposta segue l'output di prima e precede il prompt. Mentre un programma
            # lavora, o disegna nello schermo alternativo, la griglia resta sua: le righe
            # cancellate lui non le ridisegnerebbe, e la risposta va soltanto in coda
            # allo storico, senza spostare l'inizio dell'output che si sta aspettando
            program_running = self._waiting_for_output_end or self._alt_screen_active
            if not program_running and self._cursor_line_start() > 0:
                self._push_grid_rows(self._cursor_line_start() - 1)
            if self._scrollback_open and self._scrollback:
                self._scrollback[-1] = self._scrollback[-1].rstrip()
                self._scrollback_open = False

            # Aggiunge l'eco del comando @st
            lines_before = len(self._scrollback)
            if cmd_echo.strip():
                self._scrollback.append(cmd_echo.rstrip())

            # La lettura della risposta parte dalla prima riga utile dopo l'eco
            first_line = len(self._scrollback)
            if not program_running:
                self._cmd_start_line_idx = first_line
                self._cmd_anchor_text = None

            for line in output_lines:
                cleaned = line.rstrip("\r\n")
                if cleaned.strip():
                    self._scrollback.append(cleaned)

            # Se l'output atteso cominciava nella griglia, le righe inserite prima di
            # lei lo spostano in avanti
            if program_running and self._cmd_start_line_idx >= lines_before:
                self._cmd_start_line_idx += len(self._scrollback) - lines_before

            dropped_before = self._dropped_lines
            self._enforce_max_lines(exact=True)
            return max(0, first_line - (self._dropped_lines - dropped_before))

    # --- Prompt ---

    def get_current_prompt_display(self) -> str:
        """
        Restituisce l'indirizzo/prompt attuale da mostrare sulla prima riga
        dell'area di input (es. 'E:\\dati\\git>').
        """
        with self._lock:
            return self._current_prompt_display

    def _cursor_prompt_split(self) -> tuple[str, str] | None:
        """Il prompt e il comando sulla riga del cursore, fino al cursore; None se il
        cursore non sta dopo un prompt riconosciuto."""
        return split_prompt_at_cursor(self._cursor_line_up_to_cursor())

    def _cursor_after_ipython_prompt(self) -> bool:
        """prompt_toolkit puo' lasciare il cursore su una riga vuota subito sotto il
        prompt di IPython: vero se la riga del cursore e' vuota e l'ultima riga scritta
        sopra di lei e' un In [n]: nudo."""
        if self._get_row_text(self._cursor_row).strip():
            return False
        for r in range(self._cursor_row - 1, -1, -1):
            candidate = self._get_row_text(r)
            if candidate.strip():
                return bool(STRONG_PROMPT_RE.match(candidate) and IPYTHON_IN_RE.match(candidate))
        return False

    def is_at_shell_prompt(self) -> bool:
        """
        Restituisce True se il terminale si trova attualmente fermo al prompt
        di una shell o di un interprete REPL (es. cmd.exe, PowerShell, Python >>>,
        IPython In [n]:). Restituisce False quando è in esecuzione un programma
        interattivo (es. gabryscola, menu(), key(), manuale()) che attende tasti.
        """
        with self._lock:
            if self._waiting_for_output_end:
                return False
            return self._cursor_prompt_split() is not None or self._cursor_after_ipython_prompt()

    def prompt_kind(self) -> str | None:
        """
        Che prompt aspetta adesso: "shell" per cmd.exe e PowerShell, "interp" per
        Python, IPython, pdb e le righe di continuazione, "generic" per gli altri
        (sqlite, una shell remota via ssh); None se un programma sta lavorando o
        aspetta una risposta. Serve a cls, che pulisce lo schermo solo nella shell, e
        ai suggerimenti, che vengono dalla cronologia della shell.
        """
        with self._lock:
            if self._waiting_for_output_end:
                return None
            split_res = self._cursor_prompt_split()
            if split_res is None:
                return "interp" if self._cursor_after_ipython_prompt() else None
            prefix = split_res[0]
            if SHELL_PROMPT_RE.match(prefix):
                return "shell"
            if STRONG_SPLIT_RE.match(prefix):
                return "interp"
            return "generic"

    def _update_detected_prompt(self) -> bool:
        """
        Analizza la riga attiva del cursore (`_cursor_row`) per aggiornare
        l'indirizzo/prompt corrente. Restituisce True se è cambiato.
        """
        split_res = self._cursor_prompt_split()
        if split_res is None:
            return False

        prompt_prefix = split_res[0].strip()
        if not prompt_prefix:
            return False

        path_match = PATH_PROMPT_RE.match(prompt_prefix)
        if path_match:
            self._last_path_prompt = prompt_prefix
            new_display = prompt_prefix
        else:
            # Se siamo in un interprete (es. '>>>' o 'In [1]:'), mostriamo sia
            # l'ultimo indirizzo sia il prompt dell'interprete
            new_display = f"{self._last_path_prompt} {prompt_prefix}"

        if new_display != self._current_prompt_display:
            self._current_prompt_display = new_display
            return True
        return False

    # --- Invio di comandi e tasti ---

    def _get_remote_command(self) -> str | None:
        """
        Il comando gia' presente sulla riga del ConPTY, dopo il prefisso del prompt e
        fino al cursore, con gli spazi finali (per esempio dopo un Tab che ha completato
        'import '). None se il cursore non sta dopo un prompt riconosciuto. Una riga di
        soli spazi, come il rientro automatico di IPython, vale come comando vuoto.
        """
        split_res = self._cursor_prompt_split()
        if split_res is None:
            return None
        cmd = split_res[1]
        return cmd if cmd.strip() else ""

    @staticmethod
    def _reconcile(remote: str | None, local: str) -> str:
        """
        Cosa scrivere al ConPTY perche' la sua riga diventi `local`: niente se e' gia'
        uguale, il resto se ne e' l'inizio, altrimenti tanti Backspace quanti sono i
        caratteri remoti e poi il testo intero. Il Backspace e' \x7f, che ConPTY consegna
        come Backspace semplice; \x08 arriverebbe come Ctrl+Backspace, che in cmd e in
        PSReadLine cancella parole intere. Senza prompt (una risposta a input()) non si
        cancella niente: non si sa cosa ci sia nella riga del programma.
        """
        if remote is None:
            return local
        if local.startswith(remote):
            return local[len(remote) :]
        return "\x7f" * len(remote) + local

    def _arm_output_tracking(self, sent_text: str):
        """Prepara il rilevamento della fine e l'inizio della risposta a quanto si sta
        per mandare. Va chiamata con il lucchetto preso."""
        self._cmd_start_line_idx = self._lines_before_cursor_line()
        # Al prompt il riferimento e' il prompt soltanto, perche' il comando che lo segue
        # (magari completato da un Tab) sta per essere riscritto; fuori da un prompt e'
        # la riga intera, che un programma puo' riscrivere con la sua risposta
        split_res = self._cursor_prompt_split()
        self._cmd_anchor_text = split_res[0] if split_res is not None else self._cursor_line_text()
        self._cmd_sent_text = sent_text
        self._awaiting_first_newline_after_cmd = True
        self._data_received_since_send = False
        self._waiting_for_output_end = True
        self._apparent_end_signalled = False
        self._startup_phase = False
        now = time.monotonic()
        self._cmd_sent_time = now
        self._last_data_time = now
        # La misura di partenza della CPU la prende il thread di lettura: la
        # fotografia dei processi qui fermerebbe l'interfaccia a ogni Invio
        self._last_cpu_total = None
        self._generation += 1

    def clear_remote_command(self):
        """
        Se sulla riga attiva di ConPTY era stato inserito del testo (es. con Tab)
        e l'utente preme Esc o invia un comando @st, pulisce il testo in ConPTY.
        """
        if not self.is_alive():
            return
        with self._lock:
            remote_cmd = self._get_remote_command()
        if remote_cmd:
            self._write("\x7f" * len(remote_cmd))

    def request_tab_completion(self, local_cmd: str) -> bool:
        """
        Sincronizza `local_cmd` con la riga attiva del ConPTY e invia `\t` (Tab)
        per attivare l'autocompletamento nativo della shell o dell'interprete.
        Restituisce False, senza mandare niente, se il completamento non c'e': mentre
        un programma lavora, fuori da un prompt riconosciuto, e nei prompt dove il Tab
        entrerebbe nella riga come carattere invisibile (il REPL base di Python, pdb).
        """
        if not self.is_alive():
            return False

        local_cmd = clean_command_text(local_cmd)
        with self._lock:
            if self._waiting_for_output_end or self._tab_pending:
                return False
            split_res = self._cursor_prompt_split()
            if split_res is None or NO_TAB_PROMPT_RE.match(split_res[0]):
                return False
            remote_cmd = split_res[1] if split_res[1].strip() else ""
            self._generation += 1
            self._tab_pending = True
            self._tab_request_time = time.monotonic()
            self._tab_data_received = False
            self._tab_bell = False
            self._tab_old_cmd = local_cmd
            self._tab_old_below = self._get_lines_below_cursor()
            to_send = self._reconcile(remote_cmd, local_cmd) + "\t"

        self._write(to_send)
        return True

    def send_command(self, cmd_text: str):
        """
        Invia un comando (seguito da Invio) al terminale e prepara il tracciamento
        dell'inizio del nuovo output.
        """
        if not self.is_alive():
            return

        cmd_text = clean_command_text(cmd_text)
        with self._lock:
            self._tab_pending = False
            remote_cmd = self._get_remote_command()
            self._arm_output_tracking(cmd_text)

            # Invia '\r' (Carriage Return nativo del tasto Invio) invece di '\r\n',
            # così programmi che usano key() / ReadConsoleInputW non ricevono un
            # secondo evento spurio '\n' (ctrl-j) dopo l'Invio.
            to_write = self._reconcile(remote_cmd, cmd_text) + "\r"

        self._write(to_write)

    def send_raw_key(self, key_seq: str):
        """
        Invia direttamente un singolo tasto (senza aggiungere Invio) al processo
        attivo nel ConPTY (usato quando si interagisce dalla finestra di output
        con programmi basati su key(), menu(), manuale(), ecc.).
        """
        if not self.is_alive() or not key_seq:
            return

        with self._lock:
            self._tab_pending = False
            self._arm_output_tracking(key_seq if key_seq.isprintable() else "")
            self._awaiting_first_newline_after_cmd = False

        self._write(key_seq)

    def send_interrupt(self):
        """
        Invia Ctrl+C (\x03) al processo nel terminale. Al prompt fermo, senza nessun
        comando in corso, non si arma il rilevamento della fine: il fuoco non deve
        saltare per il ^C e il prompt nuovo.
        """
        if not self.is_alive():
            return
        with self._lock:
            self._tab_pending = False
            if self._waiting_for_output_end or self._cursor_prompt_split() is None:
                self._arm_output_tracking("")
                self._awaiting_first_newline_after_cmd = False
        self._write("\x03")

    def _write(self, text: str):
        """Scrive al ConPTY, se la sessione e' ancora aperta."""
        pty = self.pty
        if pty is None or not text:
            return
        with contextlib.suppress(WinptyError):
            pty.write(text)

    # --- Loop di lettura e analisi ---

    def _loop(self):
        state = {"dirty": False, "last_ui_flush": 0.0}
        while not self._stop_event.is_set():
            try:
                if not self._loop_step(state):
                    break
            except Exception as exc:  # noqa: BLE001 - il thread di lettura non deve morire
                # Un errore imprevisto nell'analisi di un blocco non deve fermare per
                # sempre la lettura: StellaTerm resterebbe muto senza dire perche'
                if self.on_error:
                    self.on_error(exc)
                time.sleep(0.05)

    def _loop_step(self, state: dict) -> bool:
        """Un giro del ciclo di lettura. Restituisce False quando la sessione e' finita."""
        pty = self.pty
        if pty is None:
            return False

        try:
            chunk = pty.read(blocking=False)
        except WinptyError:
            chunk = ""

        now = time.monotonic()

        if chunk:
            self._process_raw_chunk(chunk, now)
            state["dirty"] = True
            if now - state["last_ui_flush"] >= 0.030:
                state["dirty"] = False
                state["last_ui_flush"] = now
                if self.on_buffer_updated and not self._tab_pending:
                    self.on_buffer_updated()
            return True

        # Nessun dato letto in questo giro: una sequenza di controllo rimasta a meta'
        # per oltre 80 ms non verra' piu' completata, e si scarta invece di stamparne
        # i pezzi come testo (la coda comincia sempre con un ESC)
        with self._lock:
            if self._raw_tail and (now - self._last_data_time) > 0.080:
                self._raw_tail = ""

        # Gestione fine autocompletamento Tab
        if self._check_if_tab_finished(now):
            state["dirty"] = False
            state["last_ui_flush"] = now
            return True

        if state["dirty"] and not self._tab_pending:
            state["dirty"] = False
            state["last_ui_flush"] = now
            if self.on_buffer_updated:
                self.on_buffer_updated()

        # Controlla se l'output del comando in corso è terminato
        finished = self._check_if_output_finished(now)
        if finished:
            with self._lock:
                prompt_changed = self._update_detected_prompt()
            if prompt_changed and self.on_prompt_changed:
                self.on_prompt_changed(self.get_current_prompt_display())
            if self.on_buffer_updated:
                self.on_buffer_updated()
            if self.on_output_finished:
                self.on_output_finished(finished == "late")

        if not self.is_alive():
            if self.on_process_exited and not self._stop_event.is_set():
                self.on_process_exited()
            return False

        # A riposo, al prompt e senza niente in sospeso, bastano 20 giri al secondo:
        # meno risvegli del processore, che sul portatile a batteria contano
        with self._lock:
            busy = self._waiting_for_output_end or self._tab_pending or self._startup_phase or bool(self._raw_tail)
        time.sleep(0.012 if busy else 0.05)
        return True

    def _check_if_tab_finished(self, now: float) -> bool:
        """
        Verifica se il round-trip dell'autocompletamento Tab si è concluso. Si chiude
        appena la riga o le righe sotto cambiano rispetto all'eco del testo mandato, o
        suona il campanello; se restano uguali si aspetta fino a TAB_WAIT secondi un
        completamento lento (PowerShell al primo Tab, IPython con jedi); oltre TAB_LIMIT
        secondi si chiude comunque, anche se i dati continuano ad arrivare.
        """
        with self._lock:
            if not self._tab_pending:
                return False

            elapsed = now - self._tab_request_time
            silence = now - self._last_data_time
            remote = self._get_remote_command()
            below_lines = self._get_lines_below_cursor()
            changed = self._tab_data_received and (
                (remote is not None and remote != self._tab_old_cmd) or below_lines != self._tab_old_below
            )
            if not (
                self._tab_bell
                or (changed and silence >= 0.075)
                or (elapsed >= TAB_WAIT and silence >= 0.075)
                or elapsed >= TAB_LIMIT
            ):
                return False

            self._tab_pending = False
            prompt_changed = self._update_detected_prompt()
            old_cmd = self._tab_old_cmd
            old_below = self._tab_old_below
            # Senza un prompt riconosciuto il campo resta com'era, invece di svuotarsi
            new_cmd = old_cmd if remote is None else remote

        if prompt_changed and self.on_prompt_changed:
            self.on_prompt_changed(self.get_current_prompt_display())
        if self.on_buffer_updated:
            self.on_buffer_updated()
        if self.on_tab_completed:
            self.on_tab_completed(new_cmd, below_lines, old_cmd, old_below)
        return True

    def _process_raw_chunk(self, chunk: str, now: float):
        """
        Analizza il blocco grezzo proveniente da ConPTY applicando tutte le
        sequenze di controllo e posizionamento alla griglia virtuale 2D.
        """
        with self._lock:
            self._last_data_time = now
            self._data_received_since_send = True
            if self._tab_pending:
                self._tab_data_received = True
            data = self._raw_tail + chunk
            self._raw_tail = ""

            incomplete_idx = self._find_incomplete_escape(data)
            if incomplete_idx is not None:
                self._raw_tail = data[incomplete_idx:]
                data = data[:incomplete_idx]

            # Cambi del titolo della finestra (OSC 0 o 2): la callback parte fuori dal lucchetto
            for match in OSC_TITLE_RE.finditer(data):
                new_title = match.group(1).strip()
                if new_title and new_title != self._window_title:
                    self._window_title = new_title
                    self._pending_titles.append(new_title)

            # Le stringhe OSC, DCS, PM e APC non disegnano nulla
            data = OSC_RE.sub("", data)
            data = DCS_RE.sub("", data)

            # Sequenze CSI e ESC nell'ordine in cui arrivano, con il testo che sta fra l'una e l'altra:
            # cosi' anche le risposte alle domande del programma (DA, DSR) vedono la griglia di quel momento
            pos = 0
            for m in TOKEN_RE.finditer(data):
                if m.start() > pos:
                    self._feed_clean_text(data[pos : m.start()])

                csi_params = m.group(1)
                csi_cmd = m.group(2)
                esc_seq = m.group(3)

                if csi_cmd is not None:
                    self._handle_csi(csi_params or "", csi_cmd)
                elif esc_seq is not None:
                    self._handle_esc(esc_seq)

                pos = m.end()

            if pos < len(data):
                self._feed_clean_text(data[pos:])

            # Il tetto si controlla una volta per blocco, e solo dopo che le righe
            # sono gia' passate nello storico
            self._enforce_max_lines()

        self._dispatch_replies_and_titles()

    def _dispatch_replies_and_titles(self):
        """Scrive al ConPTY le risposte accumulate e annuncia i titoli nuovi, senza tenere il lucchetto."""
        with self._lock:
            replies, self._pending_replies = self._pending_replies, []
            titles, self._pending_titles = self._pending_titles, []
        pty = self.pty
        if pty is not None and replies:
            with contextlib.suppress(WinptyError):
                for reply in replies:
                    pty.write(reply)
        if self.on_title_changed:
            for title in titles:
                self.on_title_changed(title)

    def _set_cursor(self, row: int, col: int):
        """Sposta il cursore dentro la griglia; ogni spostamento annulla l'a capo in sospeso."""
        self._cursor_row = max(0, min(self.rows - 1, row))
        self._cursor_col = max(0, min(self.cols - 1, col))
        self._wrap_pending = False

    def _handle_esc(self, esc_seq: str):
        """Gestisce le sequenze ESC non-CSI (es. \x1b7 DECSC, \x1b8 DECRC, \x1bD IND, \x1bM RI)."""
        if esc_seq == "7":
            # DECSC: Save Cursor
            self._saved_cursor = (self._cursor_row, self._cursor_col)
        elif esc_seq == "8":
            # DECRC: Restore Cursor
            self._set_cursor(*self._saved_cursor)
        elif esc_seq == "D":
            # IND: Index (sposta giù di una riga con eventuale scroll)
            self._wrap_pending = False
            self._linefeed()
        elif esc_seq == "E":
            # NEL: Next Line
            self._set_cursor(self._cursor_row, 0)
            self._linefeed()
        elif esc_seq == "M":
            # RI: Reverse Index (sposta su di una riga con eventuale scroll in giù)
            self._wrap_pending = False
            if self._cursor_row == self._scroll_top:
                self._scroll_down_region(self._scroll_top, self._scroll_bottom, 1)
            elif self._cursor_row > 0:
                self._cursor_row -= 1
        elif esc_seq == "c":
            # RIS: Reset to Initial State. Lo schermo si svuota e lo stato del terminale
            # torna quello iniziale; lo storico resta, come in Windows Terminal
            self._alt_screen_active = False
            self._saved_primary_grid = None
            if self._scrollback_open and self._scrollback:
                self._scrollback[-1] = self._scrollback[-1].rstrip()
            self._scrollback_open = False
            self._grid = self._make_empty_grid()
            self._set_cursor(0, 0)
            self._saved_cursor = (0, 0)
            self._scroll_top = 0
            self._scroll_bottom = self.rows - 1
            self._autowrap = True
            self._last_printed_char = " "

    @staticmethod
    def _parse_int_params(params: str, default: int = 1) -> list[int]:
        cleaned = params.lstrip("?<=>!")
        if not cleaned:
            return [default]
        out: list[int] = []
        for part in cleaned.split(";"):
            part = part.strip()
            if not part:
                out.append(default)
            else:
                try:
                    out.append(int(part))
                except ValueError:
                    out.append(default)
        return out

    def _handle_csi(self, params: str, cmd_char: str):
        """Esegue le sequenze CSI direttamente sulla griglia 2D in memoria."""
        # Sequenze private DEC (\x1b[?...)
        if params.startswith("?"):
            priv_nums = self._parse_int_params(params, 0)
            if cmd_char in ("h", "l") and 7 in priv_nums:
                # DECAWM: a capo automatico all'ultima colonna, acceso o spento
                self._autowrap = cmd_char == "h"
                self._wrap_pending = False
            if cmd_char in ("h", "l") and any(
                p in (47, 1047, 1049) for p in priv_nums
            ):
                if cmd_char == "h" and not self._alt_screen_active:
                    self._alt_screen_active = True
                    self._saved_primary_grid = self._grid
                    self._saved_primary_cursor = (self._cursor_row, self._cursor_col)
                    self._grid = self._make_empty_grid()
                    self._set_cursor(0, 0)
                elif cmd_char == "l" and self._alt_screen_active:
                    # L'ultima schermata della TUI resta leggibile nello storico, ma dopo
                    # cio' che c'era prima di lei: prima passano nello storico le righe
                    # dello schermo principale sopra il cursore, cioe' l'output precedente
                    # e il comando che ha aperto la TUI, poi la schermata della TUI
                    tui_lines = [txt for txt in self._grid_lines(self.rows - 1) if txt.strip()]
                    self._alt_screen_active = False
                    if self._saved_primary_grid is not None:
                        self._grid = self._saved_primary_grid
                        self._saved_primary_grid = None
                    self._set_cursor(*self._saved_primary_cursor)
                    if self._cursor_line_start() > 0:
                        self._push_grid_rows(self._cursor_line_start() - 1)
                    for line in tui_lines:
                        self._push_to_scrollback(line)
            return

        # Le sequenze con un prefisso privato > < = (protocollo della tastiera di kitty,
        # modi del titolo di xterm, DA secondario) non sono comandi standard: si ignorano
        if params[:1] in ("<", "=", ">"):
            return

        # Ignora sequenze con caratteri intermedi (es. " q" per lo stile del cursore)
        if len(cmd_char) > 1:
            return

        if cmd_char == "A":
            # CUU: Cursor Up
            n = max(1, self._parse_int_params(params, 1)[0])
            self._set_cursor(self._cursor_row - n, self._cursor_col)
        elif cmd_char in ("B", "e"):
            # CUD / VPR: Cursor Down
            n = max(1, self._parse_int_params(params, 1)[0])
            self._set_cursor(self._cursor_row + n, self._cursor_col)
        elif cmd_char in ("C", "a"):
            # CUF / HPR: Cursor Forward
            n = max(1, self._parse_int_params(params, 1)[0])
            self._set_cursor(self._cursor_row, self._cursor_col + n)
        elif cmd_char == "D":
            # CUB: Cursor Backward
            n = max(1, self._parse_int_params(params, 1)[0])
            self._set_cursor(self._cursor_row, self._cursor_col - n)
        elif cmd_char == "E":
            # CNL: Cursor Next Line
            n = max(1, self._parse_int_params(params, 1)[0])
            self._set_cursor(self._cursor_row + n, 0)
        elif cmd_char == "F":
            # CPL: Cursor Preceding Line
            n = max(1, self._parse_int_params(params, 1)[0])
            self._set_cursor(self._cursor_row - n, 0)
        elif cmd_char in ("G", "`"):
            # CHA / HPA: Cursor Horizontal Absolute (1-based)
            col_1 = max(1, self._parse_int_params(params, 1)[0])
            self._set_cursor(self._cursor_row, col_1 - 1)
        elif cmd_char == "d":
            # VPA: Line Position Absolute (1-based)
            row_1 = max(1, self._parse_int_params(params, 1)[0])
            self._set_cursor(row_1 - 1, self._cursor_col)
        elif cmd_char in ("H", "f"):
            # CUP / HVP: Cursor Position (row;col 1-based)
            vals = self._parse_int_params(params, 1)
            row_1 = vals[0] if len(vals) >= 1 else 1
            col_1 = vals[1] if len(vals) >= 2 else 1
            self._set_cursor(max(1, row_1) - 1, max(1, col_1) - 1)
        elif cmd_char == "s":
            # SCOSC: Save Cursor Position
            self._saved_cursor = (self._cursor_row, self._cursor_col)
        elif cmd_char == "u":
            # SCORC: Restore Cursor Position
            self._set_cursor(*self._saved_cursor)
        elif cmd_char == "J":
            # ED: Erase in Display
            mode = self._parse_int_params(params, 0)[0]
            self._wrap_pending = False
            if mode == 0:
                # Dal cursore a fine schermo
                row = self._grid[self._cursor_row]
                for c in range(self._cursor_col, self.cols):
                    row[c] = " "
                row.wrapped = False
                for r in range(self._cursor_row + 1, self.rows):
                    self._grid[r] = self._new_row()
            elif mode == 1:
                # Da inizio schermo al cursore
                for r in range(self._cursor_row):
                    self._grid[r] = self._new_row()
                for c in range(self._cursor_col + 1):
                    self._grid[self._cursor_row][c] = " "
            elif mode == 2:
                # Tutto lo schermo visibile, con il cursore che resta dov'e'. Le righe scritte
                # passano prima nello storico, come fa Windows Terminal: un programma che pulisce
                # lo schermo non si porta via cio' che l'utente aveva davanti
                if not self._alt_screen_active:
                    self._push_grid_rows(self.rows - 1)
                self._grid = self._make_empty_grid()
            elif mode == 3:
                # Lo storico, che cls cancella mandando ED 2 e poi ED 3; nello schermo
                # alternativo lo storico e' quello dello schermo principale e resta
                if not self._alt_screen_active:
                    self._scrollback.clear()
                    self._scrollback_open = False
                    self._cmd_start_line_idx = 0
                    self._cmd_anchor_text = None
                    self._epoch += 1
        elif cmd_char == "K":
            # EL: Erase in Line
            mode = self._parse_int_params(params, 0)[0]
            self._wrap_pending = False
            row = self._grid[self._cursor_row]
            if mode == 0:
                for c in range(self._cursor_col, self.cols):
                    row[c] = " "
                row.wrapped = False
            elif mode == 1:
                for c in range(self._cursor_col + 1):
                    row[c] = " "
            elif mode == 2:
                self._grid[self._cursor_row] = self._new_row()
        elif cmd_char == "c":
            # DA: Device Attributes primari (ESC [ c o ESC [ 0 c), che ConPTY chiede
            # all'avvio e di cui aspetta la risposta
            if params in ("", "0"):
                self._pending_replies.append("\x1b[?1;2c")
        elif cmd_char == "n":
            # DSR: stato del terminale (5) e posizione del cursore (6), contata da 1,
            # calcolata qui e non prima: vale la griglia con il testo arrivato fin qui
            if params == "5":
                self._pending_replies.append("\x1b[0n")
            elif params == "6":
                self._pending_replies.append(f"\x1b[{self._cursor_row + 1};{self._cursor_col + 1}R")
        elif cmd_char == "X":
            # ECH: Erase Characters
            n = max(1, self._parse_int_params(params, 1)[0])
            end_c = min(self.cols, self._cursor_col + n)
            for c in range(self._cursor_col, end_c):
                self._grid[self._cursor_row][c] = " "
        elif cmd_char == "P":
            # DCH: Delete Characters
            n = max(1, self._parse_int_params(params, 1)[0])
            row_list = self._grid[self._cursor_row]
            del row_list[self._cursor_col : min(self.cols, self._cursor_col + n)]
            while len(row_list) < self.cols:
                row_list.append(" ")
        elif cmd_char == "@":
            # ICH: Insert Blank Characters (oltre la larghezza della riga non serve contare)
            n = min(self.cols, max(1, self._parse_int_params(params, 1)[0]))
            row_list = self._grid[self._cursor_row]
            row_list[self._cursor_col : self._cursor_col] = [" "] * n
            del row_list[self.cols :]
        elif cmd_char == "b":
            # REP: Repeat Preceding Graphic Character
            n = max(1, self._parse_int_params(params, 1)[0])
            self._feed_clean_text(self._last_printed_char * min(n, self.cols))
        elif cmd_char == "L":
            # IL: Insert Lines
            n = max(1, self._parse_int_params(params, 1)[0])
            if self._scroll_top <= self._cursor_row <= self._scroll_bottom:
                self._scroll_down_region(self._cursor_row, self._scroll_bottom, n)
            self._set_cursor(self._cursor_row, 0)
        elif cmd_char == "M":
            # DL: Delete Lines
            n = max(1, self._parse_int_params(params, 1)[0])
            if self._scroll_top <= self._cursor_row <= self._scroll_bottom:
                self._scroll_up_region(
                    self._cursor_row, self._scroll_bottom, n, push_to_scrollback=False
                )
            self._set_cursor(self._cursor_row, 0)
        elif cmd_char == "S":
            # SU: Scroll Up
            n = max(1, self._parse_int_params(params, 1)[0])
            self._scroll_up_region(
                self._scroll_top,
                self._scroll_bottom,
                n,
                push_to_scrollback=(self._scroll_top == 0),
            )
        elif cmd_char == "T":
            # SD: Scroll Down
            n = max(1, self._parse_int_params(params, 1)[0])
            self._scroll_down_region(self._scroll_top, self._scroll_bottom, n)
        elif cmd_char == "r":
            # DECSTBM: Set Top and Bottom Margins
            vals = self._parse_int_params(params, 0)
            top = (vals[0] if len(vals) >= 1 and vals[0] > 0 else 1) - 1
            bottom = (vals[1] if len(vals) >= 2 and vals[1] > 0 else self.rows) - 1
            if 0 <= top < bottom < self.rows:
                self._scroll_top = top
                self._scroll_bottom = bottom
                self._set_cursor(0, 0)

    def _scroll_up_region(
        self, top: int, bottom: int, count: int = 1, push_to_scrollback: bool = True
    ):
        """Scorre verso l'alto la regione `[top .. bottom]` di `count` righe."""
        # Oltre l'altezza della regione ogni giro in piu' sposterebbe solo righe vuote
        for _ in range(min(count, bottom - top + 1)):
            if push_to_scrollback and top == 0 and not self._alt_screen_active:
                self._push_to_scrollback(self._row_text_for_join(0), self._grid[0].wrapped)
            for r in range(top, bottom):
                self._grid[r] = self._grid[r + 1]
            self._grid[bottom] = self._new_row()

    def _scroll_down_region(self, top: int, bottom: int, count: int = 1):
        """Scorre verso il basso la regione `[top .. bottom]` di `count` righe."""
        for _ in range(min(count, bottom - top + 1)):
            for r in range(bottom, top, -1):
                self._grid[r] = self._grid[r - 1]
            self._grid[top] = self._new_row()

    def _linefeed(self):
        """Avanza il cursore alla riga successiva, scorrendo la griglia se necessario."""
        self._awaiting_first_newline_after_cmd = False
        if self._cursor_row == self._scroll_bottom:
            self._scroll_up_region(
                self._scroll_top,
                self._scroll_bottom,
                1,
                push_to_scrollback=(self._scroll_top == 0),
            )
        elif self._cursor_row < self.rows - 1:
            self._cursor_row += 1

    def _wrap_to_next_line(self):
        """L'a capo automatico: la riga corrente continua nella successiva."""
        self._grid[self._cursor_row].wrapped = True
        self._cursor_col = 0
        self._wrap_pending = False
        self._linefeed()

    @staticmethod
    def _find_incomplete_escape(s: str) -> int | None:
        """
        Se la fine della stringa contiene una sequenza di escape ANSI/OSC non ancora
        completata, restituisce l'indice del '\x1b' da cui rimandarla al chunk successivo.
        """
        # Una stringa OSC, DCS, PM o APC aperta e non chiusa si rimanda tutta, dal suo
        # inizio: anche quando il blocco finisce fra l'ESC e la barra del terminatore,
        # dove l'ultimo ESC da solo non direbbe che il titolo era ancora a meta'
        open_string = OPEN_STRING_RE.search(s)
        if open_string:
            return open_string.start()
        last_esc = s.rfind("\x1b")
        if last_esc == -1:
            return None
        tail = s[last_esc:]
        if tail == "\x1b":
            return last_esc
        second = tail[1]
        if second == "[":
            for ch in tail[2:]:
                if "\x40" <= ch <= "\x7e":
                    return None
            return last_esc
        if " " <= second <= "/":
            for ch in tail[2:]:
                if "\x30" <= ch <= "\x7e":
                    return None
            return last_esc
        return None

    def _write_simple_run(self, run: str):
        """Scrive nella griglia una corsa di caratteri stretti e non combinanti, a fette."""
        while run:
            if self._wrap_pending:
                self._wrap_to_next_line()
            row = self._grid[self._cursor_row]
            space = self.cols - self._cursor_col
            piece = run[:space]
            row[self._cursor_col : self._cursor_col + len(piece)] = piece
            run = run[len(piece) :]
            if self._cursor_col + len(piece) < self.cols:
                self._cursor_col += len(piece)
            elif self._autowrap:
                # Scritta anche l'ultima colonna: il cursore resta li', e il prossimo
                # carattere andra' a capo
                self._cursor_col = self.cols - 1
                self._wrap_pending = True
            else:
                # Senza a capo automatico i caratteri in piu' si sovrappongono all'ultima cella
                self._cursor_col = self.cols - 1
                if run:
                    row[self.cols - 1] = run[-1]
                    run = ""

    def _feed_clean_text(self, text: str):
        """
        Scrive caratteri nella griglia virtuale 2D gestendo '\r', '\n', '\x08', '\t',
        i caratteri larghi, quelli combinanti e l'a capo automatico in sospeso.
        """
        for m in _TEXT_RUN_RE.finditer(text):
            chunk = m.group()
            if len(chunk) > 1 or _SIMPLE_CHAR_RE.match(chunk):
                self._write_simple_run(chunk)
                self._last_printed_char = chunk[-1]
                continue
            ch = chunk
            if ch == "\r":
                self._set_cursor(self._cursor_row, 0)
            elif ch in ("\n", "\x0b", "\x0c"):
                self._wrap_pending = False
                self._linefeed()
            elif ch == "\x08":
                self._set_cursor(self._cursor_row, self._cursor_col - 1)
            elif ch == "\x07":
                # Il campanello non e' testo; durante un Tab dice che non c'e' completamento
                if self._tab_pending:
                    self._tab_bell = True
            elif ch == "\t":
                # La tabulazione sposta il cursore al prossimo multiplo di 8 senza
                # cancellare cio' che scavalca, come nei terminali veri
                next_tab = ((self._cursor_col // 8) + 1) * 8
                self._set_cursor(self._cursor_row, min(self.cols - 1, next_tab))
            elif ch < " " or "\x7f" <= ch <= "\x9f":
                # Gli altri controlli, DEL e i controlli C1 non si stampano
                continue
            elif unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
                # Un segno combinante (un accento staccato) o un carattere di formato
                # (il selettore delle emoji, il giunto di larghezza zero) si unisce al
                # carattere che lo precede, senza occupare una cella sua
                col = self._cursor_col if self._wrap_pending else self._cursor_col - 1
                if col >= 0:
                    self._grid[self._cursor_row][col] += ch
            elif unicodedata.east_asian_width(ch) in ("W", "F"):
                # Un carattere largo (ideogrammi, emoji) occupa due celle, come in
                # ConPTY: la seconda resta senza testo, cosi' le colonne dei
                # posizionamenti successivi tornano e la riga letta resta intatta
                if self._wrap_pending or self._cursor_col >= self.cols - 1:
                    self._wrap_to_next_line()
                row = self._grid[self._cursor_row]
                row[self._cursor_col] = ch
                row[self._cursor_col + 1] = ""
                self._last_printed_char = ch
                if self._cursor_col + 2 < self.cols:
                    self._cursor_col += 2
                else:
                    self._cursor_col = self.cols - 1
                    self._wrap_pending = self._autowrap
            else:
                self._write_simple_run(ch)
                self._last_printed_char = ch

    # --- Rilevamento della fine dell'output ---

    def _check_if_output_finished(self, now: float) -> str | None:
        """
        Determina se il comando inviato ha terminato di produrre output ed è
        pronto per restituire il focus all'utente. Restituisce "finished" per la fine,
        "late" per la fine vera arrivata dopo una fine apparente, None altrimenti.
        """
        with self._lock:
            if self._tab_pending:
                return None
            if not self._waiting_for_output_end and not self._startup_phase:
                return None

            silence_duration = now - self._last_data_time
            if silence_duration < 0.110:
                return None

            if (
                not self._startup_phase
                and self._awaiting_first_newline_after_cmd
                and (now - self._cmd_sent_time) < 0.250
            ):
                return None

            at_prompt = self._cursor_prompt_split() is not None or self._cursor_after_ipython_prompt()
            row_has_text = bool(self._get_row_text(self._cursor_row).strip())
            startup_phase = self._startup_phase
            data_received = self._data_received_since_send
            apparent_end = self._apparent_end_signalled
            generation = self._generation
            last_data_time = self._last_data_time

        result = None
        # Caso 1: il cursore sta dopo un prompt riconosciuto, e la shell non ha programmi
        # attivi o l'albero dei processi non consuma CPU
        if at_prompt:
            tree, cpu_idle = self._sample_process_tree(now)
            active_children = None
            if tree is not None:
                active_children = [
                    name
                    for _pid, name in tree[1:]
                    if name not in ("conhost.exe", "openconsole.exe")
                ]
            if active_children == [] or cpu_idle:
                if startup_phase:
                    with self._lock:
                        self._startup_phase = False
                        self._update_detected_prompt()
                    if self.on_prompt_changed:
                        self.on_prompt_changed(self.get_current_prompt_display())
                    return None
                result = "late" if apparent_end else "finished"

        # Caso 2: Programma interattivo (es. gabryscola con key(), menu(), manuale(),
        # oppure input()/dgt()) fermo in attesa di un tasto o di una risposta. Si segnala
        # una volta sola, e il rilevamento resta armato per il prompt che tornera'
        elif not startup_phase and data_received and not apparent_end:
            needed = WAIT_SILENCE_WITH_TEXT if row_has_text else WAIT_SILENCE_EMPTY_ROW
            if silence_duration >= needed:
                _tree, cpu_idle = self._sample_process_tree(now)
                if cpu_idle:
                    result = "finished"

        if result is None:
            return None
        with self._lock:
            # La misura della CPU si prende senza lucchetto: se intanto e' partito un
            # comando nuovo, o sono arrivati dati, la decisione non vale piu'
            if self._generation != generation or self._last_data_time != last_data_time or self._tab_pending:
                return None
            if at_prompt:
                self._waiting_for_output_end = False
                self._apparent_end_signalled = False
            else:
                self._apparent_end_signalled = True
        return result

    def _sample_process_tree(self, now: float) -> tuple[list[tuple[int, str]] | None, bool | None]:
        """
        Misura l'albero dei processi della sessione, al piu' una volta ogni
        CPU_SAMPLE_INTERVAL secondi: restituisce l'ultimo albero misurato e se fra
        le ultime due misure l'albero non ha consumato CPU (None se la misura non
        e' stata rifatta o se manca il termine di paragone). La fotografia dei
        processi costa, e su un intervallo piu' breve il tempo di CPU, che Windows
        conta a scatti di 15,6 ms, puo' restare fermo anche per un programma che
        sta lavorando. Si chiama dal thread di lettura e senza il lucchetto.
        """
        pty = self.pty
        if pty is None:
            return None, None
        with self._lock:
            previous = self._last_cpu_total
            if previous is not None and now - self._last_cpu_check_time < CPU_SAMPLE_INTERVAL:
                return self._last_tree, None
        tree, cpu_now = get_process_tree_info(pty.pid)
        with self._lock:
            self._last_cpu_total = cpu_now
            self._last_cpu_check_time = now
            self._last_tree = tree
        if previous is None:
            return tree, None
        return tree, cpu_now == previous
