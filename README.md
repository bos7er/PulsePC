# PulsePC 1.1 — optymalizator i diagnostyka Windows

![Logo PulsePC](assets/pulsepc.png)

**PulsePC** to lokalna aplikacja dla Windows 10/11 z graficznym interfejsem. Wersja gotowa do spakowania jako **pojedynczy plik `PulsePC.exe`**, który nie wymaga instalacji Pythona na komputerze użytkownika. Projekt zawiera automatyczne budowanie `.exe` na GitHubie.

## Funkcje

| Moduł | Działanie |
|---|---|
| Panel | Aktualne zużycie CPU, RAM, dysku i czas od uruchomienia Windows |
| Procesy | Podgląd uruchomionych procesów i wykorzystania zasobów |
| Czyszczenie | Skanowanie i ręczne usuwanie starych plików wyłącznie z `%LOCALAPPDATA%\Temp` |
| Autostart | Odczyt wpisów `Run` z rejestru użytkownika i komputera |
| **Rejestr Windows** | **Wyłączanie pojedynczych wpisów autostartu bieżącego użytkownika (`HKCU\...\Run`), zapis pliku `.reg` przed zmianą i możliwość przywrócenia** |
| Rekomendacje | Wskazówki wynikające z rzeczywistych pomiarów |
| Raport | Eksport JSON bez pełnych komend autostartu i prywatnych ścieżek |

**Ważne:** aplikacja nie jest „magicznym czyścicielem rejestru”. Nie usuwa losowych kluczy, nie modyfikuje ustawień systemowych ani haseł, nie wyłącza zabezpieczeń Windows i nie potrzebuje praw administratora. Wpisy `HKLM` są tylko do odczytu.

## Pobieranie gotowego `PulsePC.exe` z GitHuba

1. Wgraj projekt do repozytorium `PulsePC` na GitHubie (instrukcja poniżej).
2. Wejdź w repozytorium → **Actions** → **Build Windows EXE**.
3. Wybierz **Run workflow**, gałąź `main`, a następnie uruchom proces.
4. Gdy pojawi się zakończony przebieg oznaczony zielonym znakiem, otwórz go.
5. W sekcji **Artifacts** pobierz `PulsePC-Windows` i rozpakuj archiwum: wewnątrz jest **`PulsePC.exe`**.

Osoby, które pobierają tak zbudowane `.exe`, nie muszą instalować Pythona.

### Publiczny link do pobrania `.exe` — GitHub Releases

Aby zamiast artefaktu ZIP udostępnić `PulsePC.exe` bezpośrednio w zakładce **Releases**, utwórz tag wersji i wypchnij go:

```powershell
git tag v1.1.0
git push origin v1.1.0
```

GitHub Actions zbuduje plik na Windows i automatycznie doda `PulsePC.exe` do wydania `v1.1.0`. Po zakończeniu będzie dostępny w **Releases → v1.1.0 → Assets**. GitHub wymaga odpowiednich uprawnień Actions do publikowania wydań.

## Dodanie projektu na GitHuba

Utwórz puste repozytorium `PulsePC` (bez generowania dodatkowego README), otwórz PowerShell w folderze projektu i wykonaj:

```powershell
git init
git add .
git commit -m "PulsePC 1.1 — Windows registry startup manager"
git branch -M main
git remote add origin https://github.com/TWOJ_LOGIN/PulsePC.git
git push -u origin main
```

Zastąp `TWOJ_LOGIN` nazwą konta. Możesz też wgrać pliki przez **Add file → Upload files**, zachowując folder `.github/workflows` i pozostałą strukturę. Jeśli uruchamianie Actions nie jest dostępne, sprawdź ustawienia repozytorium i uprawnienia workflow.

## Lokalny start z Pythonem

Na Windows 10/11 z Pythonem 3.10+:

```powershell
py -m pip install -r requirements.txt
py main.py
```

## Lokalna budowa `.exe`

Na Windows uruchom dwukrotnie `build.bat`. Skrypt pobierze zależności i PyInstaller, a następnie utworzy:

```text
dist\PulsePC.exe
```

Można też uruchomić ręcznie:

```powershell
py -m pip install -r requirements.txt pyinstaller
py -m PyInstaller --noconfirm --clean --onefile --windowed --name PulsePC --icon assets\pulsepc.ico --add-data "assets;assets" main.py
```

## Jak działa moduł „Rejestr Windows”

1. W aplikacji wybierz **Rejestr Windows**.
2. Zobaczysz wpisy autostartu należące do bieżącego użytkownika. Zaznacz konkretny wpis, jeśli **wiesz, że jest zbędny**.
3. Kliknij **Wyłącz zaznaczony** i potwierdź zmianę. Przed wyłączeniem aplikacja zapisze kopię `.reg` w `%LOCALAPPDATA%\PulsePC\KopieRejestru`.
4. Wpis zostanie przeniesiony z `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` do `HKCU\Software\PulsePC\DisabledStartup`.
5. Aby go ponownie włączyć, zaznacz pozycję „Wyłączony” i kliknij **Przywróć zaznaczony**.

To wpływa na uruchamianie wskazanego programu **przy następnym logowaniu**, a nie na bieżący proces. Nie wyłączaj w ciemno programów związanych z bezpieczeństwem, synchronizacją danych lub sprzętem. Aplikacja sprawdza, czy wpis nie zmienił się od ostatniego odczytu, i nie nadpisuje istniejących wpisów o tej samej nazwie.

**Uwaga:** Program działa na wartościach `REG_SZ` i `REG_EXPAND_SZ`. Nie analizuje wszystkich miejsc autostartu (np. harmonogramu zadań i usług) i nie przeprowadza ogólnego „czyszczenia rejestru”.

## Bezpieczeństwo

- Brak konta, telemetrii, transferu danych i prośby o uprawnienia administratora.
- Zmiana rejestru dotyczy wyłącznie wskazanego wpisu w `HKCU\...\Run` i wymaga potwierdzenia.
- Każde wyłączenie tworzy pojedynczy plik kopii `.reg`, który można też zaimportować systemowym Edytorem rejestru. Zachowaj kopię do czasu sprawdzenia działania systemu.
- Czyszczenie plików TEMP jest osobną, dobrowolną funkcją; skanuje tylko pliki starsze niż 7 dni. Nie można go cofnąć. Nie usuwaj plików, których przeznaczenia nie znasz.
- Pliki `.exe` tworzone PyInstallerem mogą wywoływać ostrzeżenie SmartScreen, ponieważ program nie ma podpisu cyfrowego. Nie wyłączaj antywirusa ani zabezpieczeń Windows, aby uruchomić niezweryfikowany plik.
- Projekt ma charakter edukacyjny. Testy jednostkowe nie zastępują testu aplikacji na prawdziwym Windows 10/11.

## Testy

```powershell
py -m unittest discover -s tests -v
```

Testy zmian rejestru działają na sztucznym backendzie — **nie modyfikują prawdziwego rejestru Windows**.

## Licencja

MIT.
