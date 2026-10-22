# StellaTerm, le prove: desktop nascosto, suoni muti, sintesi finta.
# Autori: Gabriele Battaglia (IZ4APU) & ClaudIA (Claude Opus 5.5, UltraCode).
# 22/10/2026, 0.5.0: nasce con la prima versione del repository, sul modello di tests/conftest.py di MeTeOra.

"""Le regole comuni delle prove.

Le finestre nascono su un desktop di Windows nascosto, creato prima che wx
ne crei una qualunque: Windows da' il primo piano anche a una finestra mai
mostrata quando riceve il fuoco, e senza questo passaggio le prove
ruberebbero il fuoco allo schermo di chi le lancia, con NVDA che ne legge i
titoli. Se il passaggio non riesce, la suite non parte. Un sorvegliante
guarda la finestra in primo piano per tutta la sessione e, alla fine, dice
se una finestra di questo processo ci e' mai arrivata.
Gli effetti sonori finiscono in una lista invece che negli altoparlanti, e
la sintesi degli screen reader e' finta.
"""

import ctypes
import os
import sys
import threading
from ctypes import wintypes

import pytest

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)

NOME_DEL_DESKTOP = f"stellaterm_prove_{os.getpid()}"
GENERIC_ALL = 0x10000000
_DESKTOP = {}
_PRIMO_PIANO = {"fermo": threading.Event(), "nostre": set()}


def _user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateDesktopW.restype = wintypes.HANDLE
    user32.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    user32.SetThreadDesktop.restype = wintypes.BOOL
    user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    return user32


def _sorvegliante(user32):
    """Ogni 100 ms guarda chi ha il primo piano, e annota le finestre di questo processo."""
    pid = wintypes.DWORD()
    while not _PRIMO_PIANO["fermo"].wait(0.1):
        hwnd = user32.GetForegroundWindow()
        if hwnd:
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == os.getpid():
                _PRIMO_PIANO["nostre"].add(hwnd)


def pytest_configure(config):
    """Il passaggio al desktop nascosto, prima che le prove creino wx.App."""
    if sys.platform != "win32" or _DESKTOP:
        return
    user32 = _user32()
    desktop = user32.CreateDesktopW(NOME_DEL_DESKTOP, None, None, 0, GENERIC_ALL, None)
    if not desktop:
        raise pytest.UsageError(f"Le prove non partono: il desktop nascosto non si crea (errore {ctypes.get_last_error()}).")
    if not user32.SetThreadDesktop(desktop):
        raise pytest.UsageError(f"Le prove non partono: il passaggio al desktop nascosto non riesce (errore {ctypes.get_last_error()}).")
    _DESKTOP["maniglia"] = desktop
    threading.Thread(target=_sorvegliante, args=(user32,), name="sorvegliante", daemon=True).start()


def pytest_terminal_summary(terminalreporter):
    _PRIMO_PIANO["fermo"].set()
    if _PRIMO_PIANO["nostre"]:
        terminalreporter.write_line(f"ATTENZIONE: finestre delle prove arrivate in primo piano: {len(_PRIMO_PIANO['nostre'])}")
    else:
        terminalreporter.write_line("Nessuna finestra delle prove e' arrivata in primo piano.")


@pytest.fixture(autouse=True)
def suoni_muti(monkeypatch):
    """Acusticator non suona: ogni play finisce in una lista, che e' il valore della fixture."""
    import GBUtils

    suonati = []
    monkeypatch.setattr(GBUtils.Acusticator, "play", lambda nome, **opzioni: suonati.append(nome) or True)
    monkeypatch.setattr(GBUtils.Acusticator, "close", lambda: None)
    return suonati


@pytest.fixture(autouse=True)
def sintesi_finta(monkeypatch):
    """La sintesi non parla mai: i testi detti finiscono in una lista, che e' il valore della fixture."""
    import GBsintesi

    detti = []

    class Finta:
        def __init__(self, chiave):
            self.chiave = chiave

        def is_active(self):
            return self.chiave == "nvda"

        def speak(self, testo, interrupt=False):
            detti.append(testo)

        def braille(self, testo):
            pass

    monkeypatch.setattr(GBsintesi, "_crea", lambda chiave: Finta(chiave))
    return detti


@pytest.fixture(scope="session")
def app():
    import wx

    applicazione = wx.App(False)
    yield applicazione
    applicazione.Destroy()
