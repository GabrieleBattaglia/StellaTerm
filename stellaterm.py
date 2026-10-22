"""
StellaTerm - Terminale accessibile per Windows in wxPython.
Autori: Gabriele Battaglia (IZ4APU), Stella (Gemini 4 Argon) & ClaudIA (Claude Opus 5.5, UltraCode).
Data concepimento: martedì 20 ottobre 2026.
Versione e data stanno in VERSIONE e DATA_VERSIONE, qui sotto.

Caratteristiche principali:
- Finestra sempre massimizzata all'avvio.
- Area superiore (sola lettura): occupa la maggior parte dello schermo e mostra
  il buffer di output privo di righe vuote, basato su schermo virtuale 2D, e si
  aggiorna a pezzi, senza spostare chi la sta leggendo.
- Area inferiore (multiriga): occupa tutta la larghezza orizzontale, riporta
  sulla prima riga l'indirizzo/prompt attuale (es. e:\\dati\\git>) e sulla
  seconda riga il contatore progressivo [n] seguito dal comando. Il cursore di
  scrittura sta sempre dopo [n] ma l'intera area è esplorabile con le frecce.
- Suggerimenti in linea dalla cronologia: Invio esegue soltanto cio' che si e'
  scritto, Freccia Destra o Fine accettano la coda proposta.
- Comandi interni @st con salvataggio permanente in stellaterm_settings.json.
- Autocompletamento nativo della shell/interprete col tasto Tab nel campo input.
- Il manuale e' README.md, mostrato con F1 senza i segni del Markdown.
"""

import contextlib
import copy
import importlib
import json
import os
import re
import threading
import traceback
from datetime import datetime

import wx
from GBsintesi import CON_IL_BRAILLE, USCITE, Sintesi
from GBUtils import Acusticator, cartella_applicazione, percorso_risorsa

from terminal_engine import MAX_BUFFER_LINES, StartError, TerminalEngine, clean_command_text

APP_NAME = "StellaTerm"
VERSIONE = "0.5.1"
DATA_VERSIONE = "22 ottobre 2026"
SETTINGS_FILENAME = "stellaterm_settings.json"
MANUAL_FILENAME = "README.md"
# La sottocartella dei buffer salvati con @st save, esclusa da git
SAVES_DIRNAME = "salvataggi"
ERROR_LOG_FILENAME = "stellaterm_errori.log"
MAX_HISTORY_COMMANDS = 1000
# Quando la cronologia e' piena, i comandi usati per ultimi non si scacciano mai:
# altrimenti ogni comando nuovo scaccerebbe il nuovo di prima
RECENT_PROTECTED = 50
MIN_BUFFER_LINES = 50
MAX_BUFFER_LIMIT = 100000
# Quanto aspettare, dopo un comando, prima di salvare la cronologia: una raffica di
# comandi costa una scrittura sola
SAVE_DELAY_MS = 3000

# Valori predefiniti delle impostazioni permanenti. La cronologia e' un dizionario
# {comando: volte che e' stato eseguito}, in ordine dal meno recente al piu' recente
DEFAULT_SETTINGS = {
    "buffer": MAX_BUFFER_LINES,
    "rgb_i_b": [11, 11, 11],
    "rgb_i_f": [100, 100, 100],
    "rgb_o_b": [6, 6, 6],
    "rgb_o_f": [92, 92, 92],
    "size_i": 12,
    "size_o": 12,
    "volume": 100,
    "history": {},
}

# Comandi @st di base sempre disponibili tra le proposte iniziali
BUILTIN_ST_SUGGESTIONS = [
    "@st help",
    "@st set",
    "@st auto",
    "@st auto_reset",
    "@st quit",
    "@st save",
    "@st buffer ",
    "@st volume ",
    "@st size_i ",
    "@st size_o ",
    "@st rgb_i_b ",
    "@st rgb_i_f ",
    "@st rgb_o_b ",
    "@st rgb_o_f ",
]

# Gli eventi sonori: per ciascuno il preset di Acu_Collection.json e l'azione che lo fa
# suonare. Ogni evento ha il suo preset e nessun preset serve due eventi, come vuole la
# regola dei suoni (la controlla tests/test_suoni.py); l'elenco per Acu_Maker,
# suoni_stellaterm.txt, si rigenera con python elenco_suoni.py
SUONI = {
    "avvio": ("apertura", "StellaTerm si apre e il fuoco va nel campo di input."),
    "chiusura": ("meditimer_chiusura", "StellaTerm si chiude."),
    "conferma_chiusura": ("mess_campana_a_martello", "Prima di chiudere, la domanda se terminare il programma che gira ancora nel terminale."),
    "invio_comando": ("partenza", "Invio manda un comando alla sessione."),
    "tasto_diretto": ("Digitazione", "Un tasto premuto nell'area di output va al programma che lo aspetta."),
    "fine_output": ("terminata", "Il comando ha finito, o il programma aspetta una risposta, e il fuoco passa all'inizio della risposta."),
    "fine_tardiva": ("concluso", "Il prompt torna dopo che il programma era gia' stato creduto in attesa: e' la fine vera, e il fuoco resta dov'e'."),
    "fuoco_su": ("spostamento_f7", "Il fuoco sale nell'area di output."),
    "fuoco_giu": ("spostamento_f5", "Il fuoco scende nel campo di input, anche con un tasto scritto, Backspace o Ctrl+V dall'area di output."),
    "pulizia_buffer": ("eliminato", "Ctrl+L, cls o clear svuotano l'area di output."),
    "comando_svuotato": ("spugna", "Esc svuota il comando che si stava scrivendo."),
    "interruzione": ("annullato", "Ctrl+C, Ctrl+Maiuscolo+C o Ctrl+Pausa mandano l'interruzione al programma."),
    "limite_cronologia": ("meteora_banda_al_limite", "Ctrl+Freccia Su o Giu' oltre il primo o l'ultimo comando della cronologia."),
    "suggerimento_unico": ("sys_tick_basso", "Ctrl+Freccia Destra o Sinistra quando c'e' un solo suggerimento."),
    "suggerimento_accettato": ("validator", "Freccia Destra o Fine accettano il suggerimento proposto."),
    "completamento": ("conferma", "Il Tab completa il comando."),
    "elenco_scelte": ("men_di_navigazione", "Il Tab mostra piu' completamenti possibili."),
    "nessuna_corrispondenza": ("meditimer_tasto_sconosciuto", "Il Tab non trova completamenti."),
    "tab_non_disponibile": ("mess_tasto_a_vuoto", "Il Tab non e' disponibile: un programma sta lavorando, o il prompt non ha completamento."),
    "impostazione_salvata": ("convalida", "Un comando @st cambia un'impostazione e la salva."),
    "buffer_salvato": ("written_ok", "@st save scrive il buffer di output in un file."),
    "salvataggio_fallito": ("mess_salvataggio_non_riuscito", "Un'impostazione o un buffer non si possono scrivere su disco."),
    "errore_comando": ("avviso_di_sistema", "Un comando @st sconosciuto o scritto male."),
    "errore_interno": ("notifica", "StellaTerm incontra un errore interno e lo annota nel registro degli errori."),
    "manuale": ("meditimer_manuale", "F1 o @st help mostrano il manuale."),
    "parametri": ("mostra", "@st set mostra i parametri, o un comando @st senza valore mostra quello attuale."),
    "tabella_suggerimenti": ("lista", "@st auto mostra la tabella dei suggerimenti."),
    "suggerimenti_azzerati": ("cancellato", "@st auto_reset cancella tutti i suggerimenti."),
}

# Volume corrente degli effetti acustici (0..100, 0 = disattivati)
_current_volume: int = 100


def imposta_volume_audio(percentuale: int):
    """Imposta il volume globale di Acusticator da 0% a 100% (0 = disattivi)."""
    global _current_volume
    _current_volume = max(0, min(100, int(percentuale)))
    if _current_volume > 0:
        # Un guasto della scheda audio non deve fermare il terminale: si resta senza suoni
        with contextlib.suppress(Exception):
            Acusticator.setup(volume=_current_volume / 100.0)


def suona(evento: str, sync: bool = False):
    """Riproduce il suono di un evento dalla collezione Acu_Collection tramite Acusticator."""
    if _current_volume <= 0:
        return
    preset = SUONI.get(evento, ("", ""))[0]
    if preset:
        with contextlib.suppress(Exception):
            Acusticator.play(preset, sync=sync)


def scalda_audio_e_voce(al_termine):
    """
    Prepara fuori dal filo della finestra cio' che costa la prima volta: numpy e scipy
    per la sintesi dei suoni, che a freddo chiedono anche cinque secondi, e la libreria
    degli screen reader. Poi fa suonare l'avvio e chiama al_termine con wx.CallAfter.
    Senza, il primo suono fermava la finestra prima ancora di dare il fuoco all'input.
    """
    try:
        if _current_volume > 0:
            Acusticator.play(SUONI["avvio"][0])
        for uscita in USCITE:
            if uscita in CON_IL_BRAILLE:
                importlib.import_module(f"accessible_output2.outputs.{uscita}")
    except Exception:  # noqa: BLE001, S110 - il riscaldamento e' facoltativo: senza, si paga al primo uso
        pass
    finally:
        with contextlib.suppress(RuntimeError):
            wx.CallAfter(al_termine)


def pct_to_rgb(pcts: list[int] | tuple[int, int, int]) -> tuple[int, int, int]:
    """Converte 3 percentuali (0..100) in una tripla RGB (0..255)."""
    r = max(0, min(255, round(int(pcts[0]) * 255 / 100)))
    g = max(0, min(255, round(int(pcts[1]) * 255 / 100)))
    b = max(0, min(255, round(int(pcts[2]) * 255 / 100)))
    return r, g, b


def leggi_percentuale(testo: str) -> int | None:
    """Una percentuale da 0 a 100 scritta dall'utente, con o senza %, anche con la
    virgola; None se non e' un numero in quell'intervallo."""
    try:
        valore = round(float(testo.strip().rstrip("%").replace(",", ".")))
    except (ValueError, OverflowError):
        return None
    return valore if 0 <= valore <= 100 else None


def u16len(testo: str) -> int:
    """La lunghezza di un testo nelle posizioni dei controlli di testo di Windows, che
    contano in UTF-16: un'emoji fuori dal piano di base vale due posizioni."""
    if testo.isascii():
        return len(testo)
    return len(testo.encode("utf-16-le")) // 2


def _as_int(valore, predefinito: int) -> int:
    try:
        return int(valore)
    except (TypeError, ValueError, OverflowError):
        return predefinito


def trim_history(storia: dict[str, int]) -> dict[str, int]:
    """
    La cronologia entro MAX_HISTORY_COMMANDS voci: escono le meno usate, e a parita' le
    meno recenti, ma mai le ultime RECENT_PROTECTED, che sono quelle appena imparate.
    L'ordine delle voci che restano non cambia.
    """
    if len(storia) <= MAX_HISTORY_COMMANDS:
        return storia
    voci = list(storia.items())
    da_togliere = len(voci) - MAX_HISTORY_COMMANDS
    candidate = voci[: max(0, len(voci) - RECENT_PROTECTED)]
    scelte = sorted(range(len(candidate)), key=lambda i: (candidate[i][1], i))[:da_togliere]
    togli = {candidate[i][0] for i in scelte}
    return {cmd: n for cmd, n in voci if cmd not in togli}


def sanitize_settings(raw) -> dict:
    """Le impostazioni lette dal file, con ogni valore controllato e riportato nel suo
    intervallo; cio' che manca o non vale prende il valore predefinito."""
    data = copy.deepcopy(DEFAULT_SETTINGS)
    if not isinstance(raw, dict):
        return data

    data["buffer"] = max(MIN_BUFFER_LINES, min(MAX_BUFFER_LIMIT, _as_int(raw.get("buffer"), MAX_BUFFER_LINES)))

    for key in ("rgb_i_b", "rgb_i_f", "rgb_o_b", "rgb_o_f"):
        val = raw.get(key)
        if isinstance(val, (list, tuple)) and len(val) == 3:
            data[key] = [max(0, min(100, _as_int(x, d))) for x, d in zip(val, DEFAULT_SETTINGS[key], strict=True)]

    for key in ("size_i", "size_o"):
        data[key] = max(6, min(72, _as_int(raw.get(key), DEFAULT_SETTINGS[key])))

    data["volume"] = max(0, min(100, _as_int(raw.get("volume"), 100)))

    raw_hist = raw.get("history")
    cleaned: dict[str, int] = {}
    if isinstance(raw_hist, dict):
        for k, v in raw_hist.items():
            if isinstance(k, str) and k.strip():
                cleaned[k.strip()] = max(1, _as_int(v, 1))
    elif isinstance(raw_hist, list):
        # Migrazione automatica da eventuale lista precedente a dizionario {comando: conteggio}
        for item in raw_hist:
            if isinstance(item, str) and item.strip():
                k = item.strip()
                cleaned[k] = cleaned.pop(k, 0) + 1
    data["history"] = trim_history(cleaned)
    return data


class SettingsManager:
    """
    Gestisce il caricamento e il salvataggio permanente di stellaterm_settings.json.
    Il salvataggio rilegge il file e vi applica soltanto cio' che questa finestra ha
    cambiato: le impostazioni toccate con @st e i comandi eseguiti. Cosi' con due
    StellaTerm aperti nessuno dei due cancella la cronologia o le scelte dell'altro.
    """

    def __init__(self):
        self.filepath = os.path.join(cartella_applicazione(), SETTINGS_FILENAME)
        self.data: dict = copy.deepcopy(DEFAULT_SETTINGS)
        # Una frase da mostrare all'avvio, se il file c'era ma non si leggeva
        self.load_error: str = ""
        # Perche' l'ultimo salvataggio non e' riuscito
        self.last_error: str = ""
        self._changed_keys: set[str] = set()
        self._history_uses: dict[str, int] = {}
        self._history_reset: bool = False
        # Vero se il file illeggibile non si e' potuto mettere da parte: allora non si
        # salva, per non scriverci sopra
        self._read_only: bool = False
        self.load()

    def _read_file(self):
        # utf-8-sig: un file ritoccato a mano e salvato con il BOM si legge lo stesso
        with open(self.filepath, encoding="utf-8-sig") as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            raise ValueError("il file non contiene un oggetto JSON")
        return loaded

    def load(self) -> dict:
        if not os.path.isfile(self.filepath):
            self.save()
            return self.data
        try:
            self.data = sanitize_settings(self._read_file())
        except (OSError, ValueError) as exc:
            self._set_aside(exc)
        return self.data

    def _set_aside(self, exc: Exception):
        """Mette da parte il file illeggibile, invece di sovrascriverlo alla chiusura."""
        self.data = copy.deepcopy(DEFAULT_SETTINGS)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        dest = f"{self.filepath}.illeggibile_{ts}"
        try:
            os.replace(self.filepath, dest)
        except OSError as exc2:
            self._read_only = True
            self.load_error = (
                f"Le impostazioni in {self.filepath} non si leggono ({exc}) e non si possono mettere da parte ({exc2}): "
                "StellaTerm usa i valori predefiniti e in questa sessione non salva niente, per non scriverci sopra."
            )
            return
        self.load_error = (
            f"Le impostazioni in {self.filepath} non si leggono ({exc}): il file e' stato messo da parte come {dest}, "
            "e StellaTerm riparte dai valori predefiniti."
        )

    def set(self, key: str, value):
        """Cambia un'impostazione, che il prossimo salvataggio scrivera' nel file."""
        self.data[key] = value
        self._changed_keys.add(key)

    def history(self) -> dict[str, int]:
        return self.data["history"]

    def use_command(self, cmd: str):
        """Conta un comando eseguito e lo porta in fondo alla cronologia, fra i piu' recenti."""
        hist = self.data["history"]
        hist[cmd] = hist.pop(cmd, 0) + 1
        self._history_uses[cmd] = self._history_uses.pop(cmd, 0) + 1
        if len(hist) > MAX_HISTORY_COMMANDS:
            self.data["history"] = trim_history(hist)

    def reset_history(self):
        self.data["history"] = {}
        self._history_uses.clear()
        self._history_reset = True

    def save(self) -> bool:
        """Scrive il file in modo atomico; restituisce False, con il motivo in last_error, se non riesce."""
        if self._read_only:
            self.last_error = "il file delle impostazioni era illeggibile e non si e' potuto mettere da parte"
            return False
        disk = None
        if os.path.isfile(self.filepath):
            with contextlib.suppress(OSError, ValueError):
                disk = sanitize_settings(self._read_file())
        if disk is not None:
            merged = disk
            for key in self._changed_keys:
                merged[key] = copy.deepcopy(self.data[key])
            hist = {} if self._history_reset else merged["history"]
            for cmd, n in self._history_uses.items():
                hist[cmd] = hist.pop(cmd, 0) + n
            merged["history"] = trim_history(hist)
        else:
            merged = sanitize_settings(self.data)
        tmp = self.filepath + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=4, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.filepath)
        except OSError as exc:
            self.last_error = str(exc)
            return False
        # La cronologia ora comprende anche i comandi dell'altra finestra; le altre
        # impostazioni restano quelle di questa, che sono quelle in uso
        self.data["history"] = merged["history"]
        self._changed_keys.clear()
        self._history_uses.clear()
        self._history_reset = False
        self.last_error = ""
        return True


def manuale_senza_markdown(righe: list[str]) -> list[str]:
    """
    Le righe del manuale in Markdown come si leggono nell'area di output: senza i
    cancelletti dei titoli, gli asterischi e gli apici inversi, che NVDA leggerebbe,
    e con i collegamenti scritti come testo seguito dall'indirizzo fra parentesi.
    """
    out = []
    for riga in righe:
        riga = re.sub(r"^#{1,6}\s+", "", riga)
        riga = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", riga)
        riga = riga.replace("**", "").replace("`", "")
        riga = re.sub(r"^(\s*)\* ", r"\1- ", riga)
        out.append(riga)
    return out


def salva_nome(argomento: str, cartella: str, ts: str) -> tuple[str | None, str]:
    """
    Il percorso dove @st save scrive il buffer, e un messaggio d'errore se il nome non
    va bene. Senza nome: stellaterm_buffer_<data e ora>.txt. Con un nome senza
    estensione si aggiungono data, ora e .txt; un'estensione scritta si rispetta. Un
    nome relativo sta nella cartella dei salvataggi. Un file che esiste gia' non si
    sovrascrive mai: al nome si aggiunge _2, _3 e cosi' via.
    """
    nome = argomento.strip().strip('"')
    if not nome:
        nome = f"stellaterm_buffer_{ts}.txt"
    _drive, resto = os.path.splitdrive(nome)
    vietati = sorted(set(re.findall(r'[<>:"|?*\x00-\x1f]', resto)))
    if vietati:
        return None, f"Errore: il nome contiene caratteri che Windows non ammette nei nomi dei file: {' '.join(vietati)}."
    radice, estensione = os.path.splitext(nome)
    if not estensione:
        nome = f"{nome}_{ts}.txt"
        radice, estensione = os.path.splitext(nome)
    percorso = nome if os.path.isabs(nome) else os.path.join(cartella, nome)
    radice, estensione = os.path.splitext(percorso)
    numero = 2
    while os.path.exists(percorso):
        percorso = f"{radice}_{numero}{estensione}"
        numero += 1
    return percorso, ""


def recupera_comando(valore: str, contatore: int, ultimo_buono: str) -> str:
    """
    Il comando da rimettere nel campo di input quando il prefisso e' stato rovinato
    (un incolla dal menu contestuale, un annulla): cio' che segue il marcatore [n],
    oppure la seconda riga senza i resti del marcatore, oppure l'ultimo comando
    valido. Mai la riga dell'indirizzo.
    """
    marker = f"[{contatore}] "
    idx = valore.find(marker)
    if idx != -1:
        return valore[idx + len(marker) :]
    if "\n" in valore:
        seconda = valore.split("\n", 1)[1]
        return re.sub(r"^\[?\d*\]?\s?", "", seconda)
    return ultimo_buono


class CustomAccessible(wx.Accessible):
    """Classe custom per MSAA per esporre il nome corretto del controllo ai lettori dello schermo."""

    def __init__(self, win, name: str):
        super().__init__(win)
        self.name = name

    def GetName(self, childId):
        # Restituisce il nome personalizzato SOLO per il controllo stesso (wx.ACC_SELF);
        # per tutto il resto risponde l'oggetto standard del controllo
        if childId == wx.ACC_SELF:
            return wx.ACC_OK, self.name
        return wx.ACC_NOT_IMPLEMENTED, ""


class OutputMirror:
    """
    Tiene il controllo di output allineato alle righe del motore cambiando soltanto
    cio' che e' cambiato: le righe uscite in testa per il tetto si tolgono, e si
    riscrive solo dalla prima riga diversa in poi. Cosi' un buffer di 5000 righe non
    si riscrive per intero 30 volte al secondo, e chi legge con le frecce resta sulla
    stessa riga e conserva la selezione. Le posizioni sono quelle UTF-16 del controllo.
    """

    def __init__(self, ctrl: wx.TextCtrl):
        self.ctrl = ctrl
        self.lines: list[str] = []
        self.first_line = 0
        self.epoch = None

    def update(self, snap, keep_reading_position: bool):
        new = snap.lines
        old = self.lines
        drop = snap.first_line - self.first_line
        full = snap.epoch != self.epoch or drop < 0 or drop >= max(1, len(old))
        ctrl = self.ctrl
        new_text = "\n".join(new)
        sel_from, sel_to = ctrl.GetSelection()
        if full:
            if ctrl.GetValue() != new_text:
                ctrl.ChangeValue(new_text)
            removed = 0
        else:
            kept = old[drop:]
            i = 0
            limit = min(len(kept), len(new))
            while i < limit and kept[i] == new[i]:
                i += 1
            if drop == 0 and i == len(kept) == len(new):
                self._remember(snap)
                return
            removed = u16len("\n".join(old[:drop])) + 1 if drop else 0
            common = "\n".join(new[:i])
            ctrl.Freeze()
            try:
                if removed:
                    ctrl.Remove(0, removed)
                ctrl.Replace(u16len(common), ctrl.GetLastPosition(), new_text[len(common) :])
            finally:
                ctrl.Thaw()
        last = ctrl.GetLastPosition()
        if keep_reading_position:
            start = max(0, min(last, sel_from - removed))
            end = max(0, min(last, sel_to - removed))
            ctrl.SetSelection(start, end)
        else:
            ctrl.SetInsertionPoint(last)
            ctrl.ShowPosition(last)
        self._remember(snap)

    def _remember(self, snap):
        self.lines = list(snap.lines)
        self.first_line = snap.first_line
        self.epoch = snap.epoch

    def position_of_line(self, index: int) -> int:
        """La posizione nel controllo dell'inizio della riga index."""
        if index <= 0:
            return 0
        return u16len("\n".join(self.lines[:index])) + 1


class StellaTermFrame(wx.Frame):
    """Finestra principale di StellaTerm."""

    def __init__(self, initial_cwd: str | None = None):
        super().__init__(
            None,
            title=f"{APP_NAME} {VERSIONE} - Prompt dei comandi",
            style=wx.DEFAULT_FRAME_STYLE,
        )

        self.settings_mgr = SettingsManager()
        self.settings = self.settings_mgr.data

        # Applica subito il volume salvato
        imposta_volume_audio(self.settings["volume"])

        # Sintesi vocale per annunci diretti allo screen reader attivo (NVDA/JAWS)
        self._sintesi = Sintesi()

        # Cronologia per Ctrl+Freccia Su / Ctrl+Freccia Giù: l'ordine del dizionario
        # salvato, dal comando meno recente al piu' recente
        self.history: list[str] = list(self.settings_mgr.history())
        self.history_index: int = len(self.history)
        self.command_counter: int = 1
        self._current_draft: str = ""

        # Stato dell'autocompletamento inline con selezione
        self._typed_prefix: str = ""
        self._active_suggestions: list[str] = []
        self._active_suggestion_idx: int = 0
        self._closing: bool = False
        # Vero mentre StellaTerm riscrive il campo da se': la guardia del prefisso tace.
        # Lo e' anche durante la costruzione, perche' cambiare carattere e colori al
        # campo fa partire EVT_TEXT prima che il prefisso ci sia
        self._repairing_input: bool = True
        self._last_good_command: str = ""
        self._sync_queued: bool = False
        self._error_reported: bool = False
        self._save_timer: wx.CallLater | None = None

        start_dir = initial_cwd or os.getcwd()
        self._current_prompt: str = f"{start_dir.rstrip('\\/')}>"

        self._build_ui()
        self.apply_visual_settings()
        self._mirror = OutputMirror(self.output_ctrl)
        self._repairing_input = False

        # L'app deve partire sempre massimizzata: wx lo ricorda fino a Show
        self.Maximize(True)

        # Avvia il motore ConPTY 2D
        self.engine = TerminalEngine(
            on_buffer_updated=self._on_engine_buffer_updated,
            on_output_finished=self._on_engine_output_finished,
            on_title_changed=self._on_engine_title_changed,
            on_process_exited=self._on_engine_process_exited,
            on_prompt_changed=self._on_engine_prompt_changed,
            on_tab_completed=self._on_engine_tab_completed,
            on_error=self._on_engine_error,
            max_lines=self.settings["buffer"],
            cwd=start_dir,
        )
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_frame_char_hook)

        try:
            self.engine.start()
        except StartError as exc:
            wx.CallAfter(self._avvio_non_riuscito, str(exc))
            return

        # All'avvio il focus parte nel campo di comando inferiore dopo [1]
        wx.CallAfter(self._initial_focus)

    def _build_ui(self):
        self.panel = wx.Panel(self)
        self.panel.SetBackgroundColour(wx.Colour(15, 15, 15))

        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # 1. Area superiore: Output del terminale (occupa tutto lo spazio orizzontale
        #    e la maggior parte di quello verticale: proporzione 4 su 5)
        self.output_ctrl = wx.TextCtrl(
            self.panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
        )
        nome_output = "Output del terminale"
        self.output_ctrl.SetName(nome_output)
        self.output_ctrl.SetAccessible(CustomAccessible(self.output_ctrl, nome_output))
        main_sizer.Add(self.output_ctrl, 4, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 4)

        # 2. Area inferiore: Input multiriga con indirizzo attuale e [n]
        #    (occupa tutto lo spazio orizzontale e proporzione 1 su 5 in verticale)
        self.input_ctrl = wx.TextCtrl(
            self.panel,
            style=wx.TE_MULTILINE | wx.TE_RICH2 | wx.TE_PROCESS_TAB,
        )
        nome_input = "Area di input"
        self.input_accessible = CustomAccessible(self.input_ctrl, nome_input)
        self.input_ctrl.SetName(nome_input)
        self.input_ctrl.SetAccessible(self.input_accessible)
        main_sizer.Add(self.input_ctrl, 1, wx.EXPAND | wx.ALL, 4)

        self.panel.SetSizer(main_sizer)

        # Eventi dell'area di output (sola lettura)
        self.output_ctrl.Bind(wx.EVT_KEY_DOWN, self._on_output_key_down)
        self.output_ctrl.Bind(wx.EVT_CHAR, self._on_output_char)

        # Eventi dell'area di input (scrittura comando). EVT_TEXT ripara il prefisso
        # quando una strada diversa dai tasti lo rovina (un annulla, il menu
        # contestuale), EVT_TEXT_PASTE porta anche l'incolla del menu nella zona del comando
        self.input_ctrl.Bind(wx.EVT_KEY_DOWN, self._on_input_key_down)
        self.input_ctrl.Bind(wx.EVT_CHAR, self._on_input_char)
        self.input_ctrl.Bind(wx.EVT_TEXT, self._on_input_text)
        self.input_ctrl.Bind(wx.EVT_TEXT_PASTE, self._on_input_paste_event)

    def apply_visual_settings(self):
        """
        Applica colori (sfondo/caratteri) e dimensioni dei caratteri a output_ctrl
        e input_ctrl secondo i valori salvati in `self.settings`.
        """
        s = self.settings

        font_out = wx.Font(
            int(s["size_o"]),
            wx.FONTFAMILY_TELETYPE,
            wx.FONTSTYLE_NORMAL,
            wx.FONTWEIGHT_NORMAL,
        )
        font_in = wx.Font(
            int(s["size_i"]),
            wx.FONTFAMILY_TELETYPE,
            wx.FONTSTYLE_NORMAL,
            wx.FONTWEIGHT_NORMAL,
        )

        out_bg = wx.Colour(*pct_to_rgb(s["rgb_o_b"]))
        out_fg = wx.Colour(*pct_to_rgb(s["rgb_o_f"]))
        in_bg = wx.Colour(*pct_to_rgb(s["rgb_i_b"]))
        in_fg = wx.Colour(*pct_to_rgb(s["rgb_i_f"]))

        # Applica a output_ctrl (incluso il testo già presente in TE_RICH2)
        self.output_ctrl.SetFont(font_out)
        self.output_ctrl.SetBackgroundColour(out_bg)
        self.output_ctrl.SetForegroundColour(out_fg)
        out_attr = wx.TextAttr(out_fg, out_bg, font_out)
        self.output_ctrl.SetDefaultStyle(out_attr)
        last_out = self.output_ctrl.GetLastPosition()
        if last_out > 0:
            self.output_ctrl.SetStyle(0, last_out, out_attr)

        # Applica a input_ctrl (incluso il testo già presente in TE_RICH2); lo stile
        # resta in self._input_style per le riscritture del campo
        self._input_style = wx.TextAttr(in_fg, in_bg, font_in)
        self.input_ctrl.SetFont(font_in)
        self.input_ctrl.SetBackgroundColour(in_bg)
        self.input_ctrl.SetForegroundColour(in_fg)
        self.input_ctrl.SetDefaultStyle(self._input_style)
        last_in = self.input_ctrl.GetLastPosition()
        if last_in > 0:
            self.input_ctrl.SetStyle(0, last_in, self._input_style)
        else:
            self._set_command_text("", cursor_at_end=True)

        self.output_ctrl.Refresh()
        self.input_ctrl.Refresh()
        self.panel.Layout()

    def _annuncia_screen_reader(self, testo: str):
        """
        Se uno screen reader (NVDA o JAWS) è attivo, pronuncia e invia al display
        braille il testo indicato (usato per autocompletamento Tab e cronologia).
        L'ordine e' quello fisso di GBsintesi: NVDA prima di JAWS.
        """
        if not testo:
            return
        for sr in USCITE:
            if sr in CON_IL_BRAILLE and self._sintesi.dici(testo, sr, "entrambi", interrompi=True):
                return

    def _initial_focus(self):
        self.input_ctrl.SetFocus()
        self.input_ctrl.SetInsertionPointEnd()
        threading.Thread(
            target=scalda_audio_e_voce, args=(self._dopo_il_riscaldamento,), name="StellaTerm-riscaldamento", daemon=True
        ).start()

    def _dopo_il_riscaldamento(self):
        """Avvisi di avvio, quando l'audio e' pronto: le impostazioni illeggibili messe da parte."""
        if self._closing or not self.settings_mgr.load_error:
            return
        self._publish_st_result(APP_NAME, [self.settings_mgr.load_error], sound="salvataggio_fallito", jump_focus=False)

    def _avvio_non_riuscito(self, messaggio: str):
        """La console non parte: lo si dice in una finestra che NVDA legge, e si chiude."""
        wx.MessageBox(messaggio, f"{APP_NAME} {VERSIONE}", wx.OK | wx.ICON_ERROR, self)
        self._closing = True
        self.Destroy()

    # --- Gestione del prefisso (indirizzo + [n] ) nell'area di input ---

    def _get_input_prefix(self) -> str:
        """Restituisce il prefisso fisso dell'area di input: '<indirizzo>\\n[<n>] '."""
        return f"{self._current_prompt}\n[{self.command_counter}] "

    def _prefix_len(self) -> int:
        """La lunghezza del prefisso nelle posizioni del controllo."""
        return u16len(self._get_input_prefix())

    def _get_command_text(self) -> str:
        """
        Estrae il comando scritto dall'utente dopo il prefisso '[n] ' nel campo di input.
        """
        full_val = self.input_ctrl.GetValue()
        prefix = self._get_input_prefix()
        if full_val.startswith(prefix):
            return full_val[len(prefix) :]
        return recupera_comando(full_val, self.command_counter, self._last_good_command)

    def _set_command_text(self, cmd_text: str, cursor_at_end: bool = True):
        """
        Imposta il testo nel campo di input mantenendo l'indirizzo sulla prima riga
        e '[n] <cmd_text>' sulla seconda riga, posizionando il cursore dopo '[n] '.
        """
        prefix = self._get_input_prefix()
        new_val = prefix + cmd_text
        if self.input_ctrl.GetValue() != new_val:
            self.input_ctrl.ChangeValue(new_val)
            # Ri-applica lo stile sul testo di input (TE_RICH2)
            self.input_ctrl.SetStyle(0, u16len(new_val), self._input_style)
        self._last_good_command = cmd_text

        target_pos = u16len(new_val) if cursor_at_end else u16len(prefix)
        self.input_ctrl.SetInsertionPoint(target_pos)
        self.input_ctrl.ShowPosition(target_pos)

    def _prepare_input_for_writing(self):
        """
        Assicura che il prefisso '<indirizzo>\\n[<n>] ' sia intatto e che il cursore
        di scrittura si trovi nella zona modificabile dopo '[<n>] '.
        """
        prefix = self._get_input_prefix()
        full_val = self.input_ctrl.GetValue()
        if not full_val.startswith(prefix):
            cmd = self._get_command_text()
            self._set_command_text(cmd, cursor_at_end=True)
            return

        prefix_len = u16len(prefix)
        sel_from, sel_to = self.input_ctrl.GetSelection()
        if sel_from != sel_to:
            if sel_from < prefix_len:
                if sel_to <= prefix_len:
                    self.input_ctrl.SetInsertionPointEnd()
                else:
                    self.input_ctrl.SetSelection(prefix_len, sel_to)
        elif self.input_ctrl.GetInsertionPoint() < prefix_len:
            self.input_ctrl.SetInsertionPointEnd()

    def _limit_selection_to_command(self) -> bool:
        """Riduce la selezione alla zona del comando; False se la selezione stava tutta
        nel prefisso, e allora non c'e' niente da cancellare."""
        prefix_len = self._prefix_len()
        sel_from, sel_to = self.input_ctrl.GetSelection()
        if sel_to <= prefix_len:
            return False
        if sel_from < prefix_len:
            self.input_ctrl.SetSelection(prefix_len, sel_to)
        return True

    def _on_input_text(self, event):
        """
        Guardia del prefisso: dopo ogni modifica dell'utente il campo deve cominciare
        con il prefisso e il comando non deve contenere a capo. Se non e' cosi' (un
        annulla, il menu contestuale, un trascinamento) il campo si ricostruisce.
        ChangeValue non genera l'evento, quindi le riscritture di StellaTerm non lo
        fanno scattare.
        """
        event.Skip()
        if self._repairing_input or self._closing:
            return
        prefix = self._get_input_prefix()
        value = self.input_ctrl.GetValue()
        if value.startswith(prefix):
            cmd = value[len(prefix) :]
            if "\n" not in cmd and "\r" not in cmd:
                self._last_good_command = cmd
                return
            repaired = cmd
        else:
            repaired = recupera_comando(value, self.command_counter, self._last_good_command)
        repaired = repaired.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        self._repairing_input = True
        try:
            self._clear_inline_suggestion_state()
            self._set_command_text(repaired, cursor_at_end=True)
        finally:
            self._repairing_input = False

    def _vai_all_input(self, suono: bool = True):
        """Porta il fuoco nel campo di input, con il cursore in fondo al comando."""
        if suono:
            suona("fuoco_giu")
        self.input_ctrl.SetFocus()
        self._prepare_input_for_writing()
        self.input_ctrl.SetInsertionPointEnd()

    def _vai_all_output(self):
        self._discard_inline_suggestion()
        suona("fuoco_su")
        self.output_ctrl.SetFocus()

    # --- Gestione scorciatoie globali della finestra ---

    def _on_frame_char_hook(self, event):
        key = event.GetKeyCode()
        ctrl = event.ControlDown() and not event.AltDown()

        # F1: Mostra il manuale nell'area di output
        if key == wx.WXK_F1:
            self._execute_st_help(cmd_echo=f"{self._current_prompt}@st help")
            return

        # F6: Alterna rapidamente tra Output sopra e Input sotto
        if key == wx.WXK_F6:
            if wx.Window.FindFocus() == self.output_ctrl:
                self._vai_all_input()
            else:
                self._vai_all_output()
            return

        # Ctrl+L: Pulisce il buffer di output (come cls)
        if ctrl and key == ord("L"):
            suona("pulizia_buffer")
            self.engine.clear_buffer()
            return

        # Ctrl+Maiusc+C: copia la selezione, se c'e', come in Windows Terminal;
        # altrimenti, come Ctrl+Pausa, interrompe il processo in corso (Ctrl+C)
        if ctrl and event.ShiftDown() and key == ord("C"):
            focused = wx.Window.FindFocus()
            if focused in (self.output_ctrl, self.input_ctrl) and not self._has_active_suggestion_selection():
                sel_from, sel_to = focused.GetSelection()
                if sel_from != sel_to:
                    focused.Copy()
                    return
            self._interrompi()
            return
        if ctrl and key == wx.WXK_CANCEL:
            self._interrompi()
            return

        event.Skip()

    def _interrompi(self):
        self._discard_inline_suggestion()
        suona("interruzione")
        self.engine.send_interrupt()

    # --- Gestione tastiera nell'area di OUTPUT (superiore, sola lettura) ---

    def _on_output_key_down(self, event):
        key = event.GetKeyCode()
        ctrl = event.ControlDown() and not event.AltDown()
        alt = event.AltDown() and not event.ControlDown()

        # Tab / Shift+Tab nell'area di output: passa al campo di input sotto con suono
        if key == wx.WXK_TAB and not ctrl and not alt:
            self._vai_all_input()
            return

        # Se è in corso un programma interattivo (es. gabryscola con key(), menu(), manuale()),
        # Esc e Backspace premuti nell'area di output vengono inviati direttamente
        # come tasti singoli al programma restando nell'area di output! Il Backspace e'
        # \x7f, che ConPTY consegna come Backspace; \x08 arriverebbe come Ctrl+Backspace
        if not self.engine.is_at_shell_prompt():
            if key == wx.WXK_ESCAPE and not ctrl and not alt:
                suona("tasto_diretto")
                self.engine.send_raw_key("\x1b")
                return
            if key == wx.WXK_BACK and not ctrl and not alt:
                suona("tasto_diretto")
                self.engine.send_raw_key("\x7f")
                return

        # Esc nell'area di output (al prompt della shell): riporta subito giù al campo di input
        if key == wx.WXK_ESCAPE:
            self._vai_all_input()
            return

        # Backspace nell'area di output (al prompt della shell): salta giù e cancella l'ultimo carattere
        if key == wx.WXK_BACK and not ctrl and not alt:
            self._discard_inline_suggestion()
            self._vai_all_input()
            cmd = self._get_command_text()
            self._set_command_text(cmd[:-1], cursor_at_end=True)
            return

        # Ctrl+V nell'area di output: salta giù e incolla nel campo di input dopo [n]
        if ctrl and not event.ShiftDown() and key == ord("V"):
            self._vai_all_input()
            self._paste_into_input()
            return

        # Ctrl+C nell'area di output: se c'è testo selezionato lo copia normalmente;
        # se NON c'è selezione, invia Ctrl+C (interruzione) al processo nel terminale
        if ctrl and not event.ShiftDown() and key == ord("C"):
            sel_from, sel_to = self.output_ctrl.GetSelection()
            if sel_from != sel_to:
                event.Skip()
                return
            self._interrompi()
            return

        event.Skip()

    def _on_output_char(self, event):
        """
        Se l'utente preme un carattere di scrittura mentre ha il focus nell'area
        di output superiore, il focus salta immediatamente nell'area di input
        inferiore dopo [n] e il carattere premuto viene aggiunto senza andare perso.
        """
        uni = event.GetUnicodeKey()
        key = event.GetKeyCode()

        ctrl = event.ControlDown()
        alt = event.AltDown()
        is_altgr = ctrl and alt
        if (ctrl or alt) and not is_altgr:
            event.Skip()
            return

        # Caratteri stampabili (spazio, lettere, numeri, punteggiatura, simboli)
        if uni != wx.WXK_NONE and uni >= 32 and uni != 127 and key not in (
            wx.WXK_RETURN,
            wx.WXK_NUMPAD_ENTER,
            wx.WXK_ESCAPE,
            wx.WXK_TAB,
        ):
            ch = chr(uni)
            # Se c'è un programma interattivo in attesa (es. gabryscola / key()),
            # resta nella finestra di output e invia subito il singolo tasto!
            if not self.engine.is_at_shell_prompt():
                suona("tasto_diretto")
                self.engine.send_raw_key(ch)
                return
            # La coda di un suggerimento non accettato non diventa comando
            self._discard_inline_suggestion()
            self._vai_all_input()
            self.input_ctrl.WriteText(ch)
            wx.CallAfter(self._update_inline_autocomplete_from_typing)
            return

        # Invio premuto nell'area di output: se un programma interattivo è in attesa
        # invia direttamente '\r' restando nell'output; altrimenti porta giù all'input.
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if not self.engine.is_at_shell_prompt():
                suona("tasto_diretto")
                self.engine.send_raw_key("\r")
                return
            self._vai_all_input()
            return

        event.Skip()

    # --- Gestione tastiera nell'area di INPUT (inferiore, multiriga) ---

    def _paste_into_input(self):
        """Incolla dagli appunti nella zona comando: gli a capo e i Tab diventano spazi,
        gli altri caratteri di controllo si tolgono, perche' alla shell arriverebbero
        come tasti (un Tab completerebbe, un Esc svuoterebbe la riga)."""
        text = ""
        if wx.TheClipboard.Open():
            try:
                if wx.TheClipboard.IsSupported(wx.DataFormat(wx.DF_UNICODETEXT)):
                    data = wx.TextDataObject()
                    wx.TheClipboard.GetData(data)
                    text = data.GetText()
            finally:
                wx.TheClipboard.Close()
        if text:
            clean = clean_command_text(text.replace("\r\n", " ").replace("\n", " ").replace("\r", " "))
            self._prepare_input_for_writing()
            self.input_ctrl.WriteText(clean)
            self._clear_inline_suggestion_state()

    def _on_input_paste_event(self, event):
        """L'incolla del menu contestuale passa dalla stessa strada di Ctrl+V."""
        self._paste_into_input()

    def _delete_previous_word(self):
        """Ctrl+Backspace: cancella la parola prima del cursore, senza mai entrare nel prefisso."""
        prefix_len = self._prefix_len()
        pos = self.input_ctrl.GetInsertionPoint()
        if pos <= prefix_len:
            return
        cmd = self._get_command_text()
        # Il cursore nella zona del comando, contato in caratteri del comando
        before_u16 = pos - prefix_len
        cut = 0
        while cut < len(cmd) and u16len(cmd[: cut + 1]) <= before_u16:
            cut += 1
        before, after = cmd[:cut], cmd[cut:]
        stripped = before.rstrip()
        start = stripped.rfind(" ") + 1
        self._set_command_text(before[:start] + after, cursor_at_end=False)
        self.input_ctrl.SetInsertionPoint(prefix_len + u16len(before[:start]))

    def _on_input_key_down(self, event):
        key = event.GetKeyCode()
        ctrl = event.ControlDown() and not event.AltDown()
        alt = event.AltDown() and not event.ControlDown()
        shift = event.ShiftDown()

        # Invio (o Invio del tastierino) esegue soltanto cio' che si e' scritto: la coda
        # di un suggerimento non accettato si scarta. Con Ctrl o Alt non fa niente, invece
        # di mettere un a capo dentro il comando
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if not ctrl and not alt and not event.AltDown():
                self._discard_inline_suggestion()
                self._on_input_enter()
            return

        # Freccia Destra o Fine accettano il suggerimento proposto
        if key in (wx.WXK_RIGHT, wx.WXK_END, wx.WXK_NUMPAD_RIGHT, wx.WXK_NUMPAD_END) and not ctrl and not alt and not shift:
            if self._has_active_suggestion_selection():
                self._accept_inline_suggestion()
                return
            event.Skip()
            return

        # Ctrl+Freccia Destra e Sinistra scorrono i suggerimenti, ma solo se uno e' gia'
        # proposto: altrimenti restano lo spostamento per parole
        if key in (wx.WXK_RIGHT, wx.WXK_LEFT) and ctrl and not shift and not alt:
            if self._has_active_suggestion_selection():
                self._cycle_inline_suggestion(+1 if key == wx.WXK_RIGHT else -1)
                return
            event.Skip()
            return

        # Shift+Tab: sale all'area di output sopra
        if key == wx.WXK_TAB and shift and not ctrl and not alt:
            self._vai_all_output()
            return

        # Tab (senza Shift): autocompletamento nativo della shell/interprete attivo
        if key == wx.WXK_TAB and not shift and not ctrl and not alt:
            self._discard_inline_suggestion()
            if not self.engine.request_tab_completion(self._get_command_text()):
                suona("tab_non_disponibile")
            return

        # Ctrl+Freccia Su: comando precedente nella cronologia
        if key == wx.WXK_UP and ctrl and not shift:
            self._discard_inline_suggestion()
            if not self.history or self.history_index <= 0:
                suona("limite_cronologia")
                return
            if self.history_index == len(self.history):
                self._current_draft = self._get_command_text()
            self.history_index -= 1
            recalled = self.history[self.history_index]
            self._set_command_text(recalled, cursor_at_end=True)
            self._annuncia_screen_reader(recalled)
            return

        # Ctrl+Freccia Giù: comando successivo nella cronologia
        if key == wx.WXK_DOWN and ctrl and not shift:
            self._discard_inline_suggestion()
            if not self.history or self.history_index >= len(self.history):
                suona("limite_cronologia")
                return
            self.history_index += 1
            recalled = self._current_draft if self.history_index == len(self.history) else self.history[self.history_index]
            self._set_command_text(recalled, cursor_at_end=True)
            if recalled:
                self._annuncia_screen_reader(recalled)
            return

        # Esc nel campo di input: se c'è un suggerimento selezionato toglie solo
        # il suggerimento; altrimenti svuota l'intero comando scritto dopo [n]
        if key == wx.WXK_ESCAPE:
            if self._discard_inline_suggestion():
                return
            if self._get_command_text():
                suona("comando_svuotato")
                self._set_command_text("", cursor_at_end=True)
                self.engine.clear_remote_command()
                self.history_index = len(self.history)
                self._current_draft = ""
            else:
                self._set_command_text("", cursor_at_end=True)
            return

        # Protezione del prefisso '<indirizzo>\n[<n>] ' contro Backspace e Canc
        prefix_len = self._prefix_len()
        sel_from, sel_to = self.input_ctrl.GetSelection()

        if key == wx.WXK_BACK and not alt:
            # Se c'è un suggerimento inline selezionato, Backspace cancella solo
            # la parte suggerita lasciando intatto ciò che l'utente aveva digitato
            if self._discard_inline_suggestion():
                return
            if sel_from != sel_to:
                if self._limit_selection_to_command():
                    event.Skip()
                return
            if ctrl:
                self._delete_previous_word()
                return
            # Con il cursore sulla riga dell'indirizzo o dentro [n] Backspace non fa
            # niente: prima cancellava in silenzio l'ultimo carattere del comando
            if self.input_ctrl.GetInsertionPoint() <= prefix_len:
                return
            event.Skip()
            return

        if key == wx.WXK_DELETE and not alt:
            if self._discard_inline_suggestion():
                return
            if sel_from != sel_to:
                if self._limit_selection_to_command():
                    event.Skip()
                return
            if self.input_ctrl.GetInsertionPoint() < prefix_len:
                return
            event.Skip()
            return

        # Muovendosi con le frecce semplici, Inizio e le pagine, la coda di un
        # suggerimento non accettato si scarta prima di spostarsi
        if key in (
            wx.WXK_LEFT,
            wx.WXK_UP,
            wx.WXK_DOWN,
            wx.WXK_HOME,
            wx.WXK_PAGEUP,
            wx.WXK_PAGEDOWN,
            wx.WXK_NUMPAD_LEFT,
            wx.WXK_NUMPAD_UP,
            wx.WXK_NUMPAD_DOWN,
            wx.WXK_NUMPAD_HOME,
        ):
            self._discard_inline_suggestion()

        # Ctrl+V o Shift+Ins: incolla sempre nella zona comando dopo [n]
        if (ctrl and not shift and key == ord("V")) or (
            shift and not ctrl and key == wx.WXK_INSERT
        ):
            self._paste_into_input()
            return

        # Ctrl+X (Taglia): impedisce di tagliare il prefisso
        if ctrl and not shift and key == ord("X"):
            if self._discard_inline_suggestion():
                return
            if sel_from != sel_to and self._limit_selection_to_command():
                event.Skip()
            return

        # Ctrl+C nel campo di input: se non c'è testo selezionato, invia Ctrl+C al processo;
        # la coda di un suggerimento non conta come selezione
        if ctrl and not shift and key == ord("C"):
            if sel_from != sel_to and not self._has_active_suggestion_selection():
                event.Skip()
                return
            self._interrompi()
            return

        # Tutte le frecce (Su, Giù, Sinistra, Destra, Home, End, PagSu, PagGiù)
        # vengono lasciate libere per esplorare il testo multiriga con NVDA/JAWS!
        event.Skip()

    def _on_input_char(self, event):
        """
        Intercetta i caratteri stampabili digitati in `input_ctrl`: se il cursore
        si trovava sulla riga dell'indirizzo o dentro `[n] `, lo sposta
        automaticamente dopo `[n] ` prima di inserire il carattere.
        """
        uni = event.GetUnicodeKey()
        key = event.GetKeyCode()
        ctrl = event.ControlDown()
        alt = event.AltDown()
        is_altgr = ctrl and alt

        if (ctrl or alt) and not is_altgr:
            event.Skip()
            return

        # Un Invio arrivato fin qui non deve mettere un a capo nel comando
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            return

        if uni != wx.WXK_NONE and uni >= 32 and uni != 127 and key not in (
            wx.WXK_ESCAPE,
            wx.WXK_TAB,
        ):
            self._prepare_input_for_writing()
            wx.CallAfter(self._update_inline_autocomplete_from_typing)

        event.Skip()

    # --- Autocompletamento inline con selezione e ordinamento per frequenza ---

    def _clear_inline_suggestion_state(self):
        """Azzera lo stato del suggerimento inline attivo."""
        self._typed_prefix = ""
        self._active_suggestions = []
        self._active_suggestion_idx = 0

    def _has_active_suggestion_selection(self) -> bool:
        """
        Restituisce True se nel campo di input c'è attualmente una coda di
        suggerimento selezionata dopo il testo digitato dall'utente.
        """
        if not self._active_suggestions or not self._typed_prefix:
            return False
        start = self._prefix_len() + u16len(self._typed_prefix)
        sel_from, sel_to = self.input_ctrl.GetSelection()
        return sel_to > sel_from and sel_from == start

    def _discard_inline_suggestion(self) -> bool:
        """Toglie la coda di un suggerimento non accettato, lasciando cio' che si e'
        scritto; True se c'era una coda da togliere."""
        if not self._has_active_suggestion_selection():
            self._clear_inline_suggestion_state()
            return False
        typed = self._typed_prefix
        self._clear_inline_suggestion_state()
        self._set_command_text(typed, cursor_at_end=True)
        return True

    def _suggestions_allowed(self, typed: str) -> bool:
        """I suggerimenti vengono dalla cronologia della shell: si propongono al prompt
        di cmd, di PowerShell o di una shell remota, non mentre si risponde a un
        programma o dentro un interprete. I comandi @st si propongono sempre."""
        if typed.lower().startswith("@"):
            return True
        return self.engine.prompt_kind() in ("shell", "generic")

    def _get_matching_suggestions(self, typed: str) -> list[str]:
        """
        Restituisce la lista dei suggerimenti che iniziano con `typed`,
        ordinati per frequenza d'uso decrescente (più scelti in cima).
        """
        if not typed or not typed.strip() or not self._suggestions_allowed(typed):
            return []

        typed_lower = typed.lower()
        candidates: dict[str, int] = dict(self.settings_mgr.history())
        for builtin_cmd in BUILTIN_ST_SUGGESTIONS:
            if builtin_cmd not in candidates:
                candidates[builtin_cmd] = 0

        matched: list[tuple[int, str]] = []
        for cmd, count in candidates.items():
            if cmd.lower().startswith(typed_lower) and len(cmd) > len(typed):
                matched.append((count, cmd))

        # Ordina per conteggio decrescente; a parità di conteggio, ordine alfabetico
        matched.sort(key=lambda item: (-item[0], item[1].lower()))
        return [cmd for _count, cmd in matched]

    def _apply_inline_suggestion(self, suggestion: str):
        """
        Mantiene normale la parte scritta dall'utente (`self._typed_prefix`) e
        aggiunge la coda del suggerimento selezionandola, così che NVDA la legga
        automaticamente.
        """
        typed = self._typed_prefix
        if not suggestion.lower().startswith(typed.lower()) or len(suggestion) <= len(typed):
            return

        suffix = suggestion[len(typed) :]
        prefix = self._get_input_prefix()
        start_sel = u16len(prefix) + u16len(typed)
        end_sel = start_sel + u16len(suffix)

        # Sostituisce solo la parte dalla fine del testo digitato in poi
        current_full = self.input_ctrl.GetValue()
        expected_head = prefix + typed
        self._repairing_input = True
        try:
            if not current_full.startswith(expected_head):
                self._set_command_text(typed + suffix, cursor_at_end=False)
            else:
                self.input_ctrl.Replace(start_sel, self.input_ctrl.GetLastPosition(), suffix)
        finally:
            self._repairing_input = False

        self.input_ctrl.SetSelection(start_sel, end_sel)

    def _update_inline_autocomplete_from_typing(self):
        """
        Chiamato subito dopo la digitazione di un carattere: cerca i suggerimenti
        compatibili con il testo scritto e seleziona il primo (il più usato).
        """
        if self._closing or not self.input_ctrl:
            return

        # Attiva il completamento inline solo se il cursore è a fine testo senza selezione
        sel_from, sel_to = self.input_ctrl.GetSelection()
        if sel_from != sel_to or self.input_ctrl.GetInsertionPoint() != self.input_ctrl.GetLastPosition():
            return

        typed = self._get_command_text()
        matches = self._get_matching_suggestions(typed)
        if not matches:
            self._clear_inline_suggestion_state()
            return

        self._typed_prefix = typed
        self._active_suggestions = matches
        self._active_suggestion_idx = 0
        self._apply_inline_suggestion(matches[0])

    def _cycle_inline_suggestion(self, step: int):
        """
        Scorre tra i suggerimenti disponibili con Ctrl+Freccia Destra (+1)
        e Ctrl+Freccia Sinistra (-1).
        """
        if len(self._active_suggestions) == 1:
            suona("suggerimento_unico")
            self._apply_inline_suggestion(self._active_suggestions[0])
            return

        self._active_suggestion_idx = (
            self._active_suggestion_idx + step
        ) % len(self._active_suggestions)
        chosen = self._active_suggestions[self._active_suggestion_idx]
        self._apply_inline_suggestion(chosen)

    def _accept_inline_suggestion(self):
        """
        Accetta il suggerimento proposto (Freccia Destra o Fine): il comando diventa la
        voce della cronologia cosi' com'e' memorizzata, maiuscole comprese, e il cursore
        va in fondo. Il conteggio sale solo quando il comando viene eseguito.
        """
        chosen = self._active_suggestions[self._active_suggestion_idx]
        self._clear_inline_suggestion_state()
        suona("suggerimento_accettato")
        self._set_command_text(chosen, cursor_at_end=True)

    # --- Gestione del dizionario dei 1000 suggerimenti e invio comandi ---

    def _registra_comando(self, cmd: str, at_shell: bool):
        """
        Conta il comando eseguito nella cronologia permanente e lo porta in fondo
        alla cronologia di Ctrl+Freccia Su. Si contano i comandi dati alla shell e i
        comandi @st; non le risposte ai programmi, ne' le righe degli interpreti, ne'
        i singoli caratteri. Il salvataggio parte qualche secondo dopo.
        """
        cmd_clean = cmd.strip()
        if len(cmd_clean) < 2:
            return
        if not cmd_clean.lower().startswith("@st") and not at_shell:
            return
        self.settings_mgr.use_command(cmd_clean)
        if cmd_clean in self.history:
            self.history.remove(cmd_clean)
        self.history.append(cmd_clean)
        if len(self.history) > MAX_HISTORY_COMMANDS:
            self.history = self.history[-MAX_HISTORY_COMMANDS:]
        if self._save_timer is not None and self._save_timer.IsRunning():
            self._save_timer.Restart(SAVE_DELAY_MS)
        else:
            self._save_timer = wx.CallLater(SAVE_DELAY_MS, self._salva_impostazioni)

    def _salva_impostazioni(self):
        if not self.settings_mgr.save() and not self._closing:
            self._publish_st_result(
                APP_NAME,
                [f"La cronologia dei comandi non si e' potuta salvare in {self.settings_mgr.filepath}: {self.settings_mgr.last_error}."],
                sound="salvataggio_fallito",
                jump_focus=False,
            )

    def _on_input_enter(self):
        """Quando si preme Invio per eseguire il comando nel campo di input."""
        self._clear_inline_suggestion_state()
        cmd = self._get_command_text()
        cmd_stripped = cmd.strip()
        kind = self.engine.prompt_kind()

        self._registra_comando(cmd_stripped, at_shell=kind in ("shell", "generic"))
        self.history_index = len(self.history)
        self._current_draft = ""

        # Avanza il progressivo comando e ripristina il campo di input
        self.command_counter += 1
        self._set_command_text("", cursor_at_end=True)

        # Intercettazione dei comandi interni @st
        if cmd_stripped.lower() == "@st" or cmd_stripped.lower().startswith("@st "):
            self.engine.clear_remote_command()
            self._handle_st_command(cmd_stripped)
            return

        # cls e clear puliscono il buffer: al prompt di cmd o di PowerShell anche lo
        # schermo della shell, con cls; in un interprete solo il buffer, senza mandare
        # niente (in Python sarebbe un NameError); altrove, per esempio in una shell
        # remota o in risposta a un programma, il testo va com'e'
        if cmd_stripped.lower() in ("cls", "clear") and kind in ("shell", "interp"):
            suona("pulizia_buffer")
            self.engine.clear_buffer()
            if kind == "shell":
                self.engine.send_command("cls")
            return

        # Suono di invio comando e inoltro alla sessione ConPTY
        suona("invio_comando")
        self.engine.send_command(cmd)

    # --- Gestore dei comandi interni @st ---

    def _saved_or_not(self, done: str) -> tuple[str, str]:
        """La frase e il suono dopo un'impostazione cambiata: salvata, o valida solo per la sessione."""
        if self.settings_mgr.save():
            return f"{done} e salvato.", "impostazione_salvata"
        return (
            f"{done} per questa sessione, ma non salvato in {self.settings_mgr.filepath}: {self.settings_mgr.last_error}.",
            "salvataggio_fallito",
        )

    def _handle_st_command(self, raw_cmd: str):
        """Interpreta ed esegue un comando @st, mostrando il risultato nell'output."""
        cmd_echo = f"{self._current_prompt}{raw_cmd}"
        parts = raw_cmd.split(None, 2)
        if len(parts) < 2:
            self._publish_st_result(
                cmd_echo,
                [
                    "Uso: @st <comando> [parametri]. Digita '@st help' per il manuale completo.",
                    "Comandi disponibili: help, quit, set, auto, auto_reset, buffer, save, rgb_i_b, rgb_i_f, rgb_o_b, rgb_o_f, size_i, size_o, volume.",
                ],
                sound="errore_comando",
            )
            return

        sub = parts[1].lower()
        arg_str = parts[2].strip() if len(parts) >= 3 else ""

        if sub == "help":
            self._execute_st_help(cmd_echo=cmd_echo)
            return

        if sub in ("quit", "exit"):
            self.Close()
            return

        if sub == "set":
            self._execute_st_set(cmd_echo=cmd_echo)
            return

        if sub == "auto":
            self._execute_st_auto(cmd_echo=cmd_echo)
            return

        if sub == "auto_reset":
            self._execute_st_auto_reset(cmd_echo=cmd_echo)
            return

        if sub == "buffer":
            self._execute_st_buffer(cmd_echo, arg_str)
            return

        if sub == "save":
            self._execute_st_save(cmd_echo=cmd_echo, custom_arg=arg_str)
            return

        if sub in ("rgb_i_b", "rgb_i_f", "rgb_o_b", "rgb_o_f"):
            self._execute_st_rgb(cmd_echo, sub, arg_str)
            return

        if sub in ("size_i", "size_o"):
            self._execute_st_size(cmd_echo, sub, arg_str)
            return

        if sub == "volume":
            self._execute_st_volume(cmd_echo, arg_str)
            return

        self._publish_st_result(
            cmd_echo,
            [
                f"Comando sconosciuto: '@st {sub}'.",
                "Digita '@st help' per consultare il manuale con l'elenco dei comandi @st.",
            ],
            sound="errore_comando",
        )

    def _execute_st_buffer(self, cmd_echo: str, arg_str: str):
        if not arg_str:
            self._publish_st_result(
                cmd_echo,
                [
                    f"Valore attuale di buffer: {self.settings['buffer']} righe.",
                    f"Uso: @st buffer <n>, con n fra {MIN_BUFFER_LINES} e {MAX_BUFFER_LIMIT} (esempio: @st buffer 8000).",
                ],
                sound="parametri",
            )
            return
        n = _as_int(arg_str.split()[0], -1)
        if not MIN_BUFFER_LINES <= n <= MAX_BUFFER_LIMIT:
            self._publish_st_result(
                cmd_echo,
                [f"Errore: specifica un numero intero di righe compreso tra {MIN_BUFFER_LINES} e {MAX_BUFFER_LIMIT} (es. @st buffer 5000)."],
                sound="errore_comando",
            )
            return

        before = len(self.engine.get_snapshot().lines)
        self.settings_mgr.set("buffer", n)
        self.engine.set_max_lines(n)
        removed = max(0, before - len(self.engine.get_snapshot().lines))
        message, sound = self._saved_or_not(f"Buffer di output impostato a {n} righe")
        lines = [message]
        if removed:
            lines.append(f"Le {removed} righe piu' vecchie, oltre il nuovo limite, sono state tolte dall'area di output.")
        self._publish_st_result(cmd_echo, lines, sound=sound)

    def _execute_st_rgb(self, cmd_echo: str, sub: str, arg_str: str):
        labels = {
            "rgb_i_b": "Colore sfondo input (rgb_i_b)",
            "rgb_i_f": "Colore caratteri input (rgb_i_f)",
            "rgb_o_b": "Colore sfondo output (rgb_o_b)",
            "rgb_o_f": "Colore caratteri output (rgb_o_f)",
        }
        if not arg_str:
            cur = self.settings[sub]
            r255, g255, b255 = pct_to_rgb(cur)
            self._publish_st_result(
                cmd_echo,
                [
                    f"{labels[sub]} attuale: {cur[0]}% {cur[1]}% {cur[2]}% (RGB: {r255}, {g255}, {b255}).",
                    f"Uso: @st {sub} <rosso%> <verde%> <blu%> (esempio: @st {sub} 0 20 0).",
                ],
                sound="parametri",
            )
            return

        tokens = [t for t in re.split(r"[\s;]+", arg_str.strip()) if t]
        if len(tokens) != 3:
            self._publish_st_result(
                cmd_echo,
                [f"Errore: '@st {sub}' richiede 3 percentuali da 0 a 100 per Rosso, Verde e Blu (es. @st {sub} 0 20 0)."],
                sound="errore_comando",
            )
            return
        pcts = [leggi_percentuale(t) for t in tokens]
        if any(p is None for p in pcts):
            self._publish_st_result(
                cmd_echo,
                [f"Errore: le 3 percentuali devono essere numeri compresi tra 0 e 100 (es. @st {sub} 100 100 100)."],
                sound="errore_comando",
            )
            return

        self.settings_mgr.set(sub, pcts)
        self.apply_visual_settings()
        r255, g255, b255 = pct_to_rgb(pcts)
        message, sound = self._saved_or_not(f"{labels[sub]} impostato a {pcts[0]}% {pcts[1]}% {pcts[2]}% (RGB: {r255}, {g255}, {b255})")
        self._publish_st_result(cmd_echo, [message], sound=sound)

    def _execute_st_size(self, cmd_echo: str, sub: str, arg_str: str):
        label = "Dimensione caratteri input (size_i)" if sub == "size_i" else "Dimensione caratteri output (size_o)"
        if not arg_str:
            self._publish_st_result(
                cmd_echo,
                [
                    f"{label} attuale: {self.settings[sub]} pt.",
                    f"Uso: @st {sub} <dimensione>, da 6 a 72 (esempio: @st {sub} 14).",
                ],
                sound="parametri",
            )
            return
        sz = _as_int(arg_str.split()[0], -1)
        if not 6 <= sz <= 72:
            self._publish_st_result(
                cmd_echo,
                [f"Errore: specifica una dimensione font intera compresa tra 6 e 72 (es. @st {sub} 14)."],
                sound="errore_comando",
            )
            return

        self.settings_mgr.set(sub, sz)
        self.apply_visual_settings()
        message, sound = self._saved_or_not(f"{label} impostata a {sz} pt")
        self._publish_st_result(cmd_echo, [message.replace(" e salvato.", " e salvata.")], sound=sound)

    def _execute_st_volume(self, cmd_echo: str, arg_str: str):
        if not arg_str:
            stato_vol = (
                "0% (disattivati)"
                if self.settings["volume"] == 0
                else f"{self.settings['volume']}%"
            )
            self._publish_st_result(
                cmd_echo,
                [
                    f"Volume effetti audio attuale: {stato_vol}.",
                    "Uso: @st volume <n>% (esempio: @st volume 70% oppure @st volume 0 per disattivare).",
                ],
                sound="parametri",
            )
            return
        vol = leggi_percentuale(arg_str.split()[0])
        if vol is None:
            self._publish_st_result(
                cmd_echo,
                ["Errore: specifica una percentuale di volume compresa tra 0 e 100 (es. @st volume 80%)."],
                sound="errore_comando",
            )
            return

        self.settings_mgr.set("volume", vol)
        imposta_volume_audio(vol)
        done = (
            "Volume effetti audio impostato a 0% (effetti audio disattivati)"
            if vol == 0
            else f"Volume effetti audio impostato a {vol}%"
        )
        message, sound = self._saved_or_not(done)
        self._publish_st_result(cmd_echo, [message], sound=sound)

    def _execute_st_help(self, cmd_echo: str):
        """Carica il manuale del progetto e lo mostra nell'area di output."""
        manual_path = percorso_risorsa(MANUAL_FILENAME)
        try:
            with open(manual_path, encoding="utf-8-sig") as f:
                lines = manuale_senza_markdown(f.read().splitlines())
        except (OSError, UnicodeDecodeError) as e:
            lines = [
                f"{APP_NAME} {VERSIONE} del {DATA_VERSIONE}: il manuale non si legge da {manual_path} ({e}).",
                "Comandi @st: @st help, @st quit, @st set, @st auto, @st auto_reset, @st buffer n, @st save, @st rgb_i_b r g b, @st rgb_i_f r g b, @st rgb_o_b r g b, @st rgb_o_f r g b, @st size_i n, @st size_o n, @st volume n%.",
            ]
        self._publish_st_result(cmd_echo, lines, sound="manuale")

    def _get_output_char_columns(self) -> int:
        """Calcola quante colonne di testo entrano orizzontalmente in `output_ctrl`."""
        client_w = self.output_ctrl.GetClientSize().width
        char_w = self.output_ctrl.GetTextExtent("M")[0]
        if client_w > 100 and char_w > 0:
            return max(20, (client_w - 28) // char_w)
        return 120

    def _execute_st_auto(self, cmd_echo: str):
        """
        Mostra una tabella a tutta larghezza orizzontale con tutti i comandi di
        autocompletamento salvati e i relativi indici di frequenza, ordinati
        per frequenza decrescente.
        """
        suggestions = self.settings_mgr.history()
        if not suggestions:
            self._publish_st_result(
                cmd_echo,
                ["Nessun comando di autocompletamento salvato."],
                sound="tabella_suggerimenti",
            )
            return

        sorted_items = sorted(
            suggestions.items(), key=lambda kv: (-kv[1], kv[0].lower())
        )
        avail_cols = self._get_output_char_columns()

        cells = [f"{cmd} ({count})" for cmd, count in sorted_items]
        max_cell_len = max(len(c) for c in cells)
        col_width = min(avail_cols, max_cell_len + 4)
        num_cols = max(1, avail_cols // col_width)
        # Se ci sono più colonne, allarga uniformemente ogni colonna per usare tutto lo spazio orizzontale
        if num_cols > 1:
            col_width = avail_cols // num_cols

        lines = [
            f"Comandi memorizzati: {len(sorted_items)} su {MAX_HISTORY_COMMANDS}, dal piu' usato al meno usato, con le volte fra parentesi."
        ]
        for i in range(0, len(cells), num_cols):
            row_cells = cells[i : i + num_cols]
            formatted_row = "".join(
                c.ljust(col_width) if idx < len(row_cells) - 1 else c
                for idx, c in enumerate(row_cells)
            )
            lines.append(formatted_row.rstrip())

        self._publish_st_result(cmd_echo, lines, sound="tabella_suggerimenti")

    def _execute_st_auto_reset(self, cmd_echo: str):
        """Cancella tutti i comandi di autocompletamento salvati."""
        self.settings_mgr.reset_history()
        self.history.clear()
        self.history_index = 0
        self._current_draft = ""
        self._clear_inline_suggestion_state()
        if self.settings_mgr.save():
            lines, sound = ["Tutti i comandi di autocompletamento salvati sono stati cancellati."], "suggerimenti_azzerati"
        else:
            lines = [
                f"I comandi di autocompletamento sono stati cancellati per questa sessione, ma il file non si e' potuto scrivere: {self.settings_mgr.last_error}."
            ]
            sound = "salvataggio_fallito"
        self._publish_st_result(cmd_echo, lines, sound=sound)

    def _execute_st_set(self, cmd_echo: str):
        """Mostra nell'area di output la lista di tutti i parametri e dei loro valori."""
        s = self.settings
        ib_rgb = pct_to_rgb(s["rgb_i_b"])
        if_rgb = pct_to_rgb(s["rgb_i_f"])
        ob_rgb = pct_to_rgb(s["rgb_o_b"])
        of_rgb = pct_to_rgb(s["rgb_o_f"])
        vol_str = f"{s['volume']}% (effetti disattivati)" if s["volume"] == 0 else f"{s['volume']}%"

        lines = [
            f"Parametri attuali di {APP_NAME} {VERSIONE} del {DATA_VERSIONE}, salvati in {self.settings_mgr.filepath}:",
            f"buffer  : {s['buffer']} righe (capienza massima del buffer di output)",
            f"size_i  : {s['size_i']} pt (dimensione caratteri campo input)",
            f"size_o  : {s['size_o']} pt (dimensione caratteri area output)",
            f"rgb_i_b : {s['rgb_i_b'][0]}% {s['rgb_i_b'][1]}% {s['rgb_i_b'][2]}% -> RGB({ib_rgb[0]}, {ib_rgb[1]}, {ib_rgb[2]}) (sfondo campo input)",
            f"rgb_i_f : {s['rgb_i_f'][0]}% {s['rgb_i_f'][1]}% {s['rgb_i_f'][2]}% -> RGB({if_rgb[0]}, {if_rgb[1]}, {if_rgb[2]}) (caratteri campo input)",
            f"rgb_o_b : {s['rgb_o_b'][0]}% {s['rgb_o_b'][1]}% {s['rgb_o_b'][2]}% -> RGB({ob_rgb[0]}, {ob_rgb[1]}, {ob_rgb[2]}) (sfondo area output)",
            f"rgb_o_f : {s['rgb_o_f'][0]}% {s['rgb_o_f'][1]}% {s['rgb_o_f'][2]}% -> RGB({of_rgb[0]}, {of_rgb[1]}, {of_rgb[2]}) (caratteri area output)",
            f"volume  : {vol_str} (volume effetti audio)",
            f"history : {len(self.settings_mgr.history())}/{MAX_HISTORY_COMMANDS} suggerimenti con frequenza d'uso salvati",
        ]
        self._publish_st_result(cmd_echo, lines, sound="parametri")

    def _execute_st_save(self, cmd_echo: str, custom_arg: str = ""):
        """Salva il buffer di output in un file .txt con timestamp, nella cartella dei salvataggi."""
        full_text, _ = self.engine.get_text_and_start_pos()
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        save_path, errore = salva_nome(custom_arg, os.path.join(cartella_applicazione(), SAVES_DIRNAME), ts)
        if save_path is None:
            self._publish_st_result(cmd_echo, [errore], sound="errore_comando")
            return

        try:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(full_text)
                if full_text and not full_text.endswith("\n"):
                    f.write("\n")
        except OSError as e:
            self._publish_st_result(
                cmd_echo,
                [f"Errore durante il salvataggio del buffer in {save_path}: {e.strerror or e}."],
                sound="salvataggio_fallito",
            )
            return
        line_count = len(full_text.splitlines()) if full_text else 0
        self._publish_st_result(
            cmd_echo,
            [f"Buffer salvato con successo in: {save_path} ({line_count} righe)."],
            sound="buffer_salvato",
        )

    def _publish_st_result(
        self, cmd_echo: str, lines: list[str], sound: str = "fine_output", jump_focus: bool = True
    ):
        """
        Aggiunge l'esito di un comando @st al buffer dell'area di output e
        sposta il focus all'inizio della risposta.
        """
        first_line = self.engine.append_internal_output(cmd_echo, lines)
        self._sync_output_text(jump_focus_to_start=jump_focus, custom_sound=sound, line_idx=first_line)

    # --- Callback dal motore ConPTY (portate sul main thread via wx.CallAfter) ---

    def _on_engine_buffer_updated(self):
        # Un aggiornamento solo in coda alla volta: se il controllo e' lento, quelli
        # arrivati nel frattempo sono gia' compresi nel prossimo
        if not self._closing and not self._sync_queued:
            self._sync_queued = True
            wx.CallAfter(self._sync_output_text)

    def _on_engine_output_finished(self, late: bool):
        if not self._closing:
            if late:
                wx.CallAfter(self._sync_output_text, False, "fine_tardiva")
            else:
                wx.CallAfter(self._sync_output_text, True)

    def _on_engine_title_changed(self, new_title: str):
        if not self._closing:
            wx.CallAfter(self._update_window_title, new_title)

    def _on_engine_process_exited(self):
        if not self._closing:
            wx.CallAfter(self.Close)

    def _on_engine_prompt_changed(self, new_prompt: str):
        if not self._closing:
            wx.CallAfter(self._update_current_prompt, new_prompt)

    def _on_engine_tab_completed(
        self,
        new_cmd: str,
        below_lines: list[str],
        old_cmd: str,
        old_below: list[str],
    ):
        if not self._closing:
            wx.CallAfter(
                self._apply_tab_completion, new_cmd, below_lines, old_cmd, old_below
            )

    def _on_engine_error(self, exc: Exception):
        if not self._closing:
            wx.CallAfter(self._registra_errore_interno, exc)

    def _registra_errore_interno(self, exc: Exception):
        """Annota un errore del thread di lettura nel registro, e lo dice la prima volta."""
        log_path = os.path.join(cartella_applicazione(), ERROR_LOG_FILENAME)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with contextlib.suppress(OSError), open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts} {APP_NAME} {VERSIONE}\n{''.join(traceback.format_exception(exc))}\n")
        if self._error_reported or self._closing:
            return
        self._error_reported = True
        self._publish_st_result(
            APP_NAME,
            [f"StellaTerm ha incontrato un errore interno e continua a lavorare; i dettagli sono in {log_path}."],
            sound="errore_interno",
            jump_focus=False,
        )

    def _update_window_title(self, sub_title: str):
        if self._closing or not self:
            return
        self.SetTitle(f"{APP_NAME} {VERSIONE} - {sub_title}")

    def _update_current_prompt(self, new_prompt: str):
        """Aggiorna l'indirizzo corrente sulla prima riga dell'area di input."""
        if self._closing or not self.input_ctrl:
            return
        if new_prompt and new_prompt != self._current_prompt:
            current_cmd = self._get_command_text()
            had_tail = self._has_active_suggestion_selection()
            typed = self._typed_prefix
            self._current_prompt = new_prompt
            if had_tail:
                self._clear_inline_suggestion_state()
                current_cmd = typed
            self._set_command_text(current_cmd, cursor_at_end=True)

    def _apply_tab_completion(
        self,
        new_cmd: str,
        below_lines: list[str],
        old_cmd: str,
        old_below: list[str],
    ):
        """
        Applica nel campo di input il risultato dell'autocompletamento Tab
        proveniente da ConPTY e riproduce il relativo feedback sonoro e vocale.
        """
        if self._closing or not self.input_ctrl:
            return
        # Se intanto l'utente ha cambiato il comando, il completamento non vale piu'
        if self._get_command_text() != old_cmd:
            return

        has_new_suggestions = bool(below_lines) and (
            below_lines != old_below or new_cmd == old_cmd
        )

        if new_cmd != old_cmd and not has_new_suggestions:
            suona("completamento")
            self._set_command_text(new_cmd, cursor_at_end=True)
            self._annuncia_screen_reader(new_cmd)
        elif has_new_suggestions:
            suona("elenco_scelte")
            if new_cmd != old_cmd:
                self._set_command_text(new_cmd, cursor_at_end=True)
            testo_scelte = " ".join(below_lines[:3])
            if new_cmd != old_cmd:
                self._annuncia_screen_reader(f"{new_cmd} - {testo_scelte}")
            else:
                self._annuncia_screen_reader(testo_scelte)
        else:
            suona("nessuna_corrispondenza")

    def _user_is_typing(self) -> bool:
        """Vero se il fuoco e' nel campo di input e c'e' gia' un comando, o una selezione:
        a fine comando il fuoco allora non salta, per non interrompere chi scrive."""
        if wx.Window.FindFocus() != self.input_ctrl:
            return False
        sel_from, sel_to = self.input_ctrl.GetSelection()
        return bool(self._get_command_text()) or sel_from != sel_to

    def _sync_output_text(
        self,
        jump_focus_to_start: bool = False,
        custom_sound: str | None = None,
        line_idx: int | None = None,
    ):
        """
        Sincronizza il testo del buffer nel wx.TextCtrl superiore, cambiando soltanto
        le righe nuove o diverse. Se `jump_focus_to_start` è True (fine dell'output
        di un comando, o risposta di un comando @st):
        - Riproduce il suono di fine output (o `custom_sound`).
        - Posiziona il cursore esattamente all'inizio del nuovo output.
        - Sposta il focus sull'area di testo superiore, se l'utente non sta gia'
          scrivendo il comando successivo.
        Con un custom_sound e senza salto, il suono soltanto.
        """
        self._sync_queued = False
        if self._closing or not self.output_ctrl:
            return

        # Assicura che l'indirizzo nel campo di input sia sempre allineato
        detected_prompt = self.engine.get_current_prompt_display()
        if detected_prompt and detected_prompt != self._current_prompt:
            self._update_current_prompt(detected_prompt)

        snap = self.engine.get_snapshot(line_idx)
        output_focused = wx.Window.FindFocus() == self.output_ctrl
        self._mirror.update(snap, keep_reading_position=output_focused)

        if custom_sound or jump_focus_to_start:
            suona(custom_sound or "fine_output")
        if not jump_focus_to_start:
            return

        target_pos = self._mirror.position_of_line(snap.start_line)
        self.output_ctrl.SetInsertionPoint(target_pos)
        self.output_ctrl.SetSelection(target_pos, target_pos)
        self.output_ctrl.ShowPosition(target_pos)
        if not self._user_is_typing():
            self.output_ctrl.SetFocus()

    def _on_close(self, event):
        if self._closing:
            event.Skip()
            return
        # Se nel terminale gira ancora un programma, chiudere lo termina: si chiede prima
        if event.CanVeto():
            programs = self.engine.running_programs()
            if programs:
                suona("conferma_chiusura")
                nomi = ", ".join(dict.fromkeys(programs))
                risposta = wx.MessageBox(
                    f"Nel terminale sta ancora girando {nomi}. Chiudendo StellaTerm verra' terminato. Chiudere comunque?",
                    f"{APP_NAME} {VERSIONE}",
                    wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
                    self,
                )
                if risposta != wx.YES:
                    event.Veto()
                    return
        self._closing = True
        if self._save_timer is not None:
            self._save_timer.Stop()
        self.settings_mgr.save()
        self.engine.stop()
        suona("chiusura", sync=True)
        with contextlib.suppress(Exception):
            Acusticator.close()
        event.Skip()


def main():
    app = wx.App(False)
    app.SetAppName(APP_NAME)
    frame = StellaTermFrame()
    if frame:
        frame.Show()
    app.MainLoop()


if __name__ == "__main__":
    main()
