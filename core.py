"""Odczyt stanu systemu i ostrożne czyszczenie plików tymczasowych.

Ta warstwa nie wymaga uprawnień administratora, nie modyfikuje rejestru,
nie kończy procesów i nie usuwa żadnych plików systemowych.
"""

from __future__ import annotations

import datetime as dt
import os
import platform
import stat
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import psutil


MIN_FILE_AGE_DAYS = 7
MAX_SCAN_FILES = 25_000


@dataclass(frozen=True)
class TempFile:
    path: str
    size: int
    modified: float


@dataclass(frozen=True)
class ScanResult:
    files: tuple[TempFile, ...]
    bytes_total: int
    skipped: int
    limited: bool
    root: str


@dataclass(frozen=True)
class CleanResult:
    removed_count: int
    removed_bytes: int
    skipped_count: int
    error_count: int


def user_temp_dir() -> Path | None:
    """Wybierz tylko standardowy folder TEMP bieżącego użytkownika Windows."""
    if os.name != "nt":
        return None
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) / "Temp" if local_app_data else Path.home() / "AppData" / "Local" / "Temp"
    # Nie ufamy zmiennym wskazującym na przypadkowy katalog nadrzędny.
    if root.name.casefold() != "temp" or root.parent.name.casefold() != "local":
        return None
    if not root.is_dir() or _is_reparse(root):
        return None
    return root


def bytes_human(value: int | float) -> str:
    x = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if x < 1024 or unit == "TB":
            return f"{x:.0f} {unit}" if unit == "B" else f"{x:.1f} {unit}"
        x /= 1024
    return "0 B"


def snapshot() -> dict:
    """Zwróć prawdziwe pomiary sprzętu; nie obiecuje fikcyjnych przyspieszeń."""
    cpu = psutil.cpu_percent(interval=0.20)
    memory = psutil.virtual_memory()
    drive = (os.environ.get("SystemDrive", "C:") + "\\") if os.name == "nt" else str(Path.home())
    try:
        disk = psutil.disk_usage(drive)
    except (OSError, ValueError):
        disk = psutil.disk_usage(str(Path.home()))
    boot = psutil.boot_time()
    return {
        "cpu_percent": cpu,
        "ram_percent": memory.percent,
        "ram_used": memory.used,
        "ram_total": memory.total,
        "disk_percent": disk.percent,
        "disk_used": disk.used,
        "disk_total": disk.total,
        "disk_free": disk.free,
        "disk_drive": drive,
        "uptime_seconds": max(0, int(time.time() - boot)),
        "processor": platform.processor() or "Nieznany procesor",
        "system": f"{platform.system()} {platform.release()}",
        "physical_cores": psutil.cpu_count(logical=False) or 0,
        "logical_cores": psutil.cpu_count(logical=True) or 0,
        "captured_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def running_processes(limit: int = 120) -> list[dict]:
    """Lista procesów tylko do odczytu, posortowana wg zajętej pamięci."""
    processes = []
    for process in psutil.process_iter(attrs=["pid", "name", "memory_info", "cpu_percent"]):
        try:
            data = process.info
            mem = data.get("memory_info")
            processes.append({
                "pid": int(data["pid"]),
                "name": str(data.get("name") or "Nieznany"),
                "memory_bytes": int(mem.rss) if mem else 0,
                "cpu_percent": float(data.get("cpu_percent") or 0),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, TypeError, ValueError):
            continue
    processes.sort(key=lambda p: p["memory_bytes"], reverse=True)
    return processes[:limit]


def startup_entries() -> list[dict]:
    """Odczytaj wpisy Run z rejestru Windows bez możliwości ich edycji."""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    keys = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", "Użytkownik"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", "Komputer"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", "Komputer (32-bit)"),
    )
    items: list[dict] = []
    for hive, path, source in keys:
        try:
            with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
                i = 0
                while True:
                    try:
                        name, command, _ = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    items.append({"name": name, "scope": source, "command": str(command)})
                    i += 1
        except (OSError, PermissionError):
            continue
    return sorted(items, key=lambda x: (x["name"].casefold(), x["scope"]))


def _is_reparse(path: Path) -> bool:
    """Wyklucz symlinki, junctions i inne punkty ponownej analizy Windows."""
    try:
        info = path.lstat()
    except (OSError, ValueError):
        return True
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _within_root(path: Path, root: Path) -> bool:
    """Ochrona przed wyjściem poza TEMP, również gdy ścieżki są względne."""
    try:
        common = os.path.commonpath([os.path.abspath(path), os.path.abspath(root)])
        return os.path.normcase(common) == os.path.normcase(os.path.abspath(root)) and path != root
    except (ValueError, OSError):
        return False


def _safe_path(path: Path, root: Path) -> bool:
    if not _within_root(path, root) or _is_reparse(root):
        return False
    cursor = path
    while cursor != root:
        if _is_reparse(cursor):
            return False
        parent = cursor.parent
        if parent == cursor:
            return False
        cursor = parent
    return True


def scan_temp(root: Path, min_age_days: int = MIN_FILE_AGE_DAYS, max_files: int = MAX_SCAN_FILES,
              now: float | None = None) -> ScanResult:
    """Wykryj *pliki* z TEMP starsze niż 7 dni; nie podążaj za linkami.

    Nie dodawaj dowolnego folderu z interfejsu. Parametr root służy do testów.
    """
    root = Path(root).absolute()
    if min_age_days < MIN_FILE_AGE_DAYS:
        raise ValueError("Minimalny wiek plików to 7 dni")
    if max_files <= 0:
        raise ValueError("max_files musi być dodatni")
    if not root.is_dir() or _is_reparse(root):
        return ScanResult((), 0, 0, False, str(root))

    deadline = (time.time() if now is None else now) - min_age_days * 86400
    found: list[TempFile] = []
    skipped = 0
    limited = False

    def onerror(_: OSError) -> None:
        nonlocal skipped
        skipped += 1

    for dirpath, dirs, files in os.walk(root, topdown=True, followlinks=False, onerror=onerror):
        folder = Path(dirpath)
        dirs[:] = [d for d in dirs if not _is_reparse(folder / d)]
        for filename in files:
            candidate = folder / filename
            if len(found) >= max_files:
                limited = True
                break
            try:
                if not _within_root(candidate, root) or _is_reparse(candidate):
                    skipped += 1
                    continue
                entry = candidate.stat(follow_symlinks=False)
                if not stat.S_ISREG(entry.st_mode) or entry.st_mtime >= deadline:
                    continue
                found.append(TempFile(str(candidate), entry.st_size, entry.st_mtime))
            except (OSError, ValueError):
                skipped += 1
        if limited:
            break
    found.sort(key=lambda item: item.size, reverse=True)
    return ScanResult(tuple(found), sum(f.size for f in found), skipped, limited, str(root))


def clean_temp(scan: ScanResult, min_age_days: int = MIN_FILE_AGE_DAYS,
               now: float | None = None) -> CleanResult:
    """Usuń wyłącznie zatwierdzone po skanowaniu pliki, po ponownej kontroli."""
    if min_age_days < MIN_FILE_AGE_DAYS:
        raise ValueError("Minimalny wiek plików to 7 dni")
    root = Path(scan.root).absolute()
    if not root.is_dir() or _is_reparse(root):
        return CleanResult(0, 0, len(scan.files), 0)
    deadline = (time.time() if now is None else now) - min_age_days * 86400
    removed = 0
    released = 0
    skipped = 0
    errors = 0
    for item in scan.files:
        target = Path(item.path).absolute()
        if not _safe_path(target, root):
            skipped += 1
            continue
        try:
            current = target.stat(follow_symlinks=False)
            if not stat.S_ISREG(current.st_mode) or current.st_mtime >= deadline:
                skipped += 1
                continue
            # Jeśli plik się zmienił od skanowania, nie dotykaj go.
            if current.st_mtime != item.modified or current.st_size != item.size:
                skipped += 1
                continue
            target.unlink()
            removed += 1
            released += current.st_size
        except FileNotFoundError:
            skipped += 1
        except (PermissionError, OSError):
            errors += 1
    return CleanResult(removed, released, skipped, errors)


def recommendations(data: dict, temp: ScanResult | None = None) -> list[dict]:
    tips: list[dict] = []
    if data.get("ram_percent", 0) >= 85:
        tips.append({"title": "Wysokie użycie RAM", "text": "Zamknij niepotrzebne programy i karty przeglądarki. Zobacz zakładkę Procesy.", "level": "uwaga"})
    if data.get("disk_percent", 0) >= 85:
        tips.append({"title": "Dysk prawie pełny", "text": "Usuń niepotrzebne pliki z własnych folderów i przejrzyj aplikacje. Nie usuwaj ręcznie plików Windows.", "level": "uwaga"})
    if data.get("cpu_percent", 0) >= 90:
        tips.append({"title": "Wysokie chwilowe użycie CPU", "text": "Sprawdź aktywne procesy. Jednorazowy pomiar nie musi oznaczać problemu.", "level": "uwaga"})
    if data.get("uptime_seconds", 0) >= 7 * 86400:
        tips.append({"title": "Długi czas pracy systemu", "text": "Jeśli komputer działa wolniej niż zwykle, zwykły restart może pomóc.", "level": "info"})
    if temp and temp.bytes_total >= 500 * 1024 * 1024:
        tips.append({"title": "Stare pliki tymczasowe", "text": "W katalogu TEMP znaleziono ponad 500 MB starych plików. Możesz je przejrzeć i świadomie usunąć.", "level": "info"})
    tips.extend([
        {"title": "Autostart", "text": "Zbyt wiele aplikacji uruchamianych razem z Windows może wydłużać start. Sprawdź listę w zakładce Autostart.", "level": "info"},
        {"title": "Aktualizacje i dysk", "text": "Zachowuj wolne miejsce na SSD i korzystaj z Windows Update. Nie używaj losowych „optymalizatorów” rejestru.", "level": "info"},
    ])
    if not any(t["level"] == "uwaga" for t in tips):
        tips.insert(0, {"title": "Brak oczywistych alarmów", "text": "Na podstawie tych pomiarów nie widać krytycznego obciążenia. Nie ma potrzeby wymuszać zmian.", "level": "ok"})
    return tips


def report_payload(data: dict, processes: Iterable[dict], startups: Iterable[dict], temp: ScanResult | None) -> dict:
    """Raport bez ścieżek do plików i komend autostartu (ochrona prywatności)."""
    return {
        "program": "PulsePC 1.1",
        "snapshot": data,
        "top_processes": list(processes)[:20],
        "startup_count": len(list(startups)),
        "old_temp_files": len(temp.files) if temp else None,
        "old_temp_bytes": temp.bytes_total if temp else None,
        "note": "Pomiary chwilowe, bez diagnozy sprzętu. Raport nie zawiera ścieżek ani komend autostartu.",
    }
