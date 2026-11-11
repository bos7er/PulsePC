"""Bezpieczna obsługa wybranych wpisów rejestru użytkownika Windows.

Dopuszczamy tylko HKCU\\...\\Run. Przed wyłączeniem pojedynczego wpisu
zapisujemy kopię .reg; żadnego zbiorczego „czyszczenia rejestru”.
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
DISABLED_KEY = r"Software\PulsePC\DisabledStartup"


@dataclass(frozen=True)
class RunEntry:
    name: str
    command: str
    kind: int
    enabled: bool


def _winreg():
    if os.name != "nt":
        raise OSError("Zmiany w rejestrze są dostępne wyłącznie w Windows.")
    import winreg
    return winreg


def _check_supported(name: str, command: str, kind: int, winreg) -> None:
    if not name or "\x00" in name or any(c in name for c in "\r\n"):
        raise ValueError("Nieprawidłowa nazwa wpisu rejestru.")
    if not isinstance(command, str) or "\x00" in command:
        raise ValueError("Nieobsługiwana wartość wpisu rejestru.")
    if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
        raise ValueError("Ta wersja obsługuje tylko REG_SZ i REG_EXPAND_SZ.")


def list_user_run() -> list[RunEntry]:
    """Pokaż tylko autostart bieżącego użytkownika i wpisy wyłączone przez PulsePC."""
    try:
        winreg = _winreg()
    except OSError:
        return []
    result: list[RunEntry] = []
    for path, enabled in ((RUN_KEY, True), (DISABLED_KEY, False)):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
                index = 0
                while True:
                    try:
                        name, command, kind = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and isinstance(command, str):
                        result.append(RunEntry(name, command, kind, enabled))
                    index += 1
        except OSError:
            pass
    return sorted(result, key=lambda e: (e.enabled, e.name.casefold()), reverse=True)


def _quote_reg(value: str) -> str:
    """Format .reg dla REG_SZ, z ucieczką znaków specjalnych."""
    if "\r" in value or "\n" in value or "\x00" in value:
        raise ValueError("Wpis z wieloliniowym tekstem nie może być bezpiecznie wyeksportowany.")
    return value.replace("\\", "\\\\").replace('"', '\\"')


def format_reg_backup(name: str, command: str, kind: int) -> str:
    """Kopia jednego wpisu; nie nadpisuje pozostałych wpisów przy imporcie."""
    winreg = _winreg()
    _check_supported(name, command, kind, winreg)
    safe_name = _quote_reg(name)
    if kind == winreg.REG_SZ:
        data = f'"{_quote_reg(command)}"'
    else:
        # REG_EXPAND_SZ = tekst UTF-16LE zakończony znakiem NUL.
        raw = command.encode("utf-16le") + b"\x00\x00"
        hexadecimal = [f"{byte:02x}" for byte in raw]
        chunks = [",".join(hexadecimal[i:i + 20]) for i in range(0, len(hexadecimal), 20)]
        data = "hex(2):" + ",\\\r\n  ".join(chunks)
    return (
        "Windows Registry Editor Version 5.00\r\n\r\n"
        "[HKEY_CURRENT_USER\\" + RUN_KEY + "]\r\n"
        f'"{safe_name}"={data}\r\n'
    )


def _backup_dir() -> Path:
    localappdata = os.environ.get("LOCALAPPDATA")
    if not localappdata:
        raise OSError("Nie znaleziono LOCALAPPDATA; kopia bezpieczeństwa nie może być zapisana.")
    return Path(localappdata) / "PulsePC" / "KopieRejestru"


def _create_backup(name: str, command: str, kind: int) -> Path:
    folder = _backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    content = format_reg_backup(name, command, kind)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = folder / f"Run-{timestamp}-{uuid.uuid4().hex[:8]}.reg"
    with path.open("xb") as file:
        file.write(b"\xff\xfe")
        file.write(content.encode("utf-16le"))
        file.flush()
        os.fsync(file.fileno())
    return path


def _read_value(key, name: str, winreg) -> tuple[str, int]:
    try:
        command, kind = winreg.QueryValueEx(key, name)
    except FileNotFoundError as exc:
        raise ValueError("Wpis już nie istnieje. Odśwież listę.") from exc
    _check_supported(name, command, kind, winreg)
    return command, kind


def disable_user_run(entry: RunEntry) -> Path:
    """Kopia .reg -> własny klucz wyłączonych wpisów -> usuń wybrany HKCU Run."""
    if not entry.enabled:
        raise ValueError("Ten wpis jest już wyłączony.")
    winreg = _winreg()
    access = winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access) as active:
        command, kind = _read_value(active, entry.name, winreg)
        if (command, kind) != (entry.command, entry.kind):
            raise ValueError("Wpis zmienił się od czasu odczytu. Odśwież listę.")
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, DISABLED_KEY, 0, access) as stored:
            try:
                winreg.QueryValueEx(stored, entry.name)
            except FileNotFoundError:
                pass
            else:
                raise ValueError("Wpis jest już w magazynie wyłączonych. Nie można go nadpisać.")
            path = _create_backup(entry.name, command, kind)
            # Sprawdź ponownie — aplikacje mogą aktualizować Run w tle.
            if _read_value(active, entry.name, winreg) != (command, kind):
                raise ValueError("Wpis zmienił się podczas operacji. Kopia .reg pozostała zapisana.")
            winreg.SetValueEx(stored, entry.name, 0, kind, command)
            try:
                winreg.DeleteValue(active, entry.name)
            except OSError:
                winreg.DeleteValue(stored, entry.name)
                raise
    return path


def restore_user_run(entry: RunEntry) -> None:
    """Przywróć wpis do autostartu, nie nadpisując nowszych ustawień."""
    if entry.enabled:
        raise ValueError("Ten wpis jest już aktywny.")
    winreg = _winreg()
    access = winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, DISABLED_KEY, 0, access) as stored:
        command, kind = _read_value(stored, entry.name, winreg)
        if (command, kind) != (entry.command, entry.kind):
            raise ValueError("Wpis zmienił się od czasu odczytu. Odśwież listę.")
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access) as active:
            try:
                winreg.QueryValueEx(active, entry.name)
            except FileNotFoundError:
                pass
            else:
                raise ValueError("Aktywny wpis o tej nazwie już istnieje. Nie nadpisujemy go.")
            winreg.SetValueEx(active, entry.name, 0, kind, command)
            try:
                winreg.DeleteValue(stored, entry.name)
            except OSError:
                # Nie usuwaj skopiowanego aktywnego wpisu: nadal jest przywrócony.
                raise OSError("Wpis przywrócono, ale nie udało się usunąć kopii z magazynu. Odśwież listę.")
