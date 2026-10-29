# Hito Uninstall

A Windows 10/11 desktop app that shows what is using your storage, cleans temporary files safely, and uninstalls programs and games completely, including their leftovers. It runs fully offline, needs no account, and ships as a single portable `.exe`.

Built with Python 3.12 + PySide6 (Qt 6) and packaged with Nuitka.

## Features

| Page | What it does |
|---|---|
| **Dashboard** | Connected drives with capacity bars, safely recoverable space, the last scan's category donut, largest folders, largest apps, quick actions. All real data. |
| **Storage Analyzer** | Multi-threaded, read-only scan of a drive or folder. Interactive **treemap** (click to drill in, right-click for actions) and **category donut** (click a category to filter). Sortable explorer with search, category filter, "Largest files" mode, Open in Explorer, Delete. |
| **Smart Cleanup** | Temp files, browser caches (every Chrome/Edge/Brave/Vivaldi/Opera/Firefox profile), thumbnail/icon caches, error reports, crash dumps, app caches (Discord, Teams, Slack, Spotify, VS Code, ...), game launcher caches (Steam, Epic, Battle.net, EA, GOG), developer caches, old logs, Recycle Bin. Grouped **Safe / Review recommended / Advanced**, with an explanation and an "after cleaning" note for each, per-file selection, an exact action preview, confirmation, and a report of real space recovered. |
| **Installed Apps & Games** | Uninstall registry (64/32-bit, per-user), MSI, Microsoft Store packages, Steam and Epic manifests. Icons, publisher, version, platform, install date, location. **Sizes are measured** from the install folder; estimates are marked `~`. Sort by largest. |
| **Deep Uninstaller** | Runs the program's official uninstaller (MSI `/x`, its own uninstaller, `Remove-AppxPackage`, Steam's uninstall), closes or asks you to close its processes, and detects when it *really* finished. Then it runs an evidence-based leftover scan (files, folders, shortcuts, registry keys and values) with confidence and risk for each item. Batch queue with per-app confirmation. |
| **Large & Duplicate Files** | Large files over 100 MB / 500 MB / 1 GB / 5 GB / 10 GB / custom. Duplicates found in three stages: size → quick signature → full BLAKE2b hash. Auto-select keeps the oldest, newest or shortest-path copy and never all copies. |
| **Settings & History** | Dry-run mode, Recycle Bin vs permanent deletion, scan options, excluded folders, custom categories, dark/light theme, **operation log**, **registry backups with restore**, scan history. |

## Safety model

Every destructive action goes through one gateway (`hito/safety/deleter.py`), which re-checks a protection policy (`hito/safety/protect.py`) at the moment of deletion.

- **Read-only by default.** Scans never change anything. Nothing runs automatically at startup except read-only scans.
- **Confirmation with an exact preview.** Every delete shows the paths, sizes and the method (Recycle Bin or permanent). You can save the list to a text file.
- **Recycle Bin by default** for files you pick yourself. Disposable caches are deleted permanently by default, which you can change in Settings.
- **Dry-run mode** simulates every delete, cleanup and registry change and writes it to the log.
- **Never removed:**
  - drive roots, `C:\Windows` (except whitelisted temp, log and dump folders), `Program Files` roots, the Windows Installer and Package Cache folders, `WindowsApps`, System Volume Information, Recovery, page/hibernation files and `Windows.old` (handled only through Windows' own tools);
  - user profile roots and known personal folders themselves;
  - browser passwords, cookies and history;
  - this app's own data;
  - files of running programs;
  - folders used by another installed program.
- **Warnings that need explicit acknowledgement:** game saves (`Saved Games`, `My Games`, `saves`) and items inside personal folders.
- **Junctions and symlinks are never followed** while scanning or deleting. Deleting a folder that contains a junction removes only the link, never its target (this is tested).
- **Registry:** every key or value is exported with `reg export` to `%LOCALAPPDATA%\HitoUninstall\backups\*.reg` before removal and can be restored from Settings & History. Core keys (`HKLM\SYSTEM`, `SOFTWARE\Microsoft`, `Classes`, the Run and Uninstall roots, ...) are never removed.
- **Administrator rights are requested only when you click "Restart as administrator".** Uninstallers that need admin trigger Windows' own UAC prompt. Nothing elevates silently.
- **Everything is logged** to `%LOCALAPPDATA%\HitoUninstall\logs\operations.jsonl`: deletions, skips with their reasons, uninstalls, registry changes and tool runs.

Undo is possible only for items moved to the Recycle Bin and for registry entries (from their backups). **Permanent deletions cannot be undone.**

### How leftover detection decides

A candidate is proposed only with ownership evidence:

| Evidence | Confidence |
|---|---|
| The install folder recorded by the app's uninstall entry; the Store package-family data folder; shortcuts or Run/App Paths entries pointing into the removed install folder; an orphaned uninstall key; a folder with the install folder's exact name that contains the app's own executables | High |
| A folder or registry key named exactly after the app, or a publisher folder that holds nothing else | Medium |
| A folder whose name only starts with the app's name | Low (never pre-selected) |

Generic names (Common, Update, Microsoft, Cache, ...) are never matched. Folders that belong to another installed program are excluded. Only **high-confidence, low-risk** items are pre-selected, and only when the uninstall was confirmed. The tool does not promise to remove 100% of an app's traces.

## Using it

1. Run `HitoUninstall.exe`. No installation is needed. The first launch unpacks the program to `%LOCALAPPDATA%\HitoUninstall\1.0.0`, so later launches are faster.
2. **Dashboard** shows drives right away. Click **Scan** on a drive to analyze it.
3. **Smart Cleanup**: review the Safe items, open **Choose files...** to untick anything, then **Clean selected...**, check the preview, and confirm.
4. **Installed Apps & Games**: select an app (or tick several) and choose **Deep uninstall**. Follow the program's own uninstaller, then review the leftovers and click **Remove selected...**.
5. Turn on **Dry-run mode** in Settings to try everything without changing anything.

Windows SmartScreen may warn about an unsigned exe. Choose *More info → Run anyway*. The exe is not code-signed.

## Run from source

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python hito_main.py
```

## Build the portable .exe

```powershell
.\build.ps1          # or double-click build.bat
```

The script:

1. creates a build venv in `%LOCALAPPDATA%\HitoUninstall-build\venv` and installs the pinned dependencies (`requirements.txt`, `requirements-build.txt`);
2. lints with pyflakes and runs the test suite;
3. compiles with Nuitka (`--onefile`, no console window, PySide6 plugin, embedded icon and version information).

On first run Nuitka downloads a MinGW64 C compiler (about 300 MB, one time) unless Visual Studio Build Tools are installed. Intermediate files are kept out of the repo. The result is `dist\HitoUninstall.exe`. Use `-SkipTests` to skip tests or `-Venv <path>` to choose another venv.

## Tests

```powershell
python -m pytest                                  # unit + offscreen GUI smoke tests
$env:HITO_INTEGRATION="1"; python -m pytest tests/test_integration_uninstall.py
```

- `test_scanner`: size roll-up, junction loops, junction double counting, hard links, cancellation, unreadable folders, categories.
- `test_classify`: location contexts, AppData caches, known folders, game libraries, custom categories.
- `test_safety`: protected locations in every context, cleanup confined to its roots, other apps' folders, browser data, saves, running programs, dry run, permanent delete and logging, warnings that need acknowledgement, deleting through junctions.
- `test_cleanup`: catalog sanity (no personal or installer-cache paths), age and pattern filters, plan execution, pruning, dry run, refusing files outside a target.
- `test_duplicates`: identical vs near-identical files, hard links, auto-select keeps one, all-copies selection refused, large-file threshold.
- `test_uninstall`: command-line parsing, MSI `/x` resolution, Store/Steam/missing-uninstaller methods, date parsing, VDF parsing, protected registry keys, leftover evidence and safety, the `.lnk` parser.
- `test_ui_smoke` (offscreen Qt): all pages build; a real scan, then delete with sizes updating; the duplicate flow keeps one copy; refusing to delete every copy; a dry run of the real cleanup catalog.
- `test_integration_uninstall` (opt-in): creates a throwaway app under HKCU with a real uninstaller and settings key, deep-uninstalls it, removes the leftovers, then restores the registry backup.

### Self-test of a build

```powershell
dist\HitoUninstall.exe --self-test report.json   # exit code 0 = every engine works in this build
```

The self-test is read-only. It checks the bundled imports, drive listing, a scan of your user folder, registry, Store and Steam discovery, the cleanup scan, and duplicate detection plus a dry-run delete in a temp folder.

### What has actually been tested (Windows 11 Home, build 26300, not elevated)

- **Unit tests:** 66 passing, plus the opt-in integration test above.
- **The built `dist\HitoUninstall.exe` (23 MB):**
  - opens its window in about 8 s on first launch and about 2 s after that, with no console window and no UAC prompt;
  - shows the custom icon and the version information;
  - closes cleanly;
  - `--self-test` passes.
- **Real data on this PC:**
  - full scan of C:\ (774k files, 222 GB, 71 s);
  - 115 apps found (registry, Store and Steam);
  - cleanup scan of 3.2 GB Safe plus 12.7 GB to review.

**Not yet tested:**
- Windows 10;
- an elevated (administrator) session;
- uninstalling real third-party programs (only the dummy app);
- Epic, Battle.net and GOG uninstalls, because none are installed on the test PC.

## Project layout

```
hito/
  app.py                 entry point, theme, error hook
  settings.py, history.py
  core/      winapi.py (ctypes Win32), fsutil.py, drives.py, categories.py, scanner.py
  cleanup/   catalog.py (targets + risk levels), engine.py (scan / plan / execute / Windows tools)
  apps/      inventory.py (registry, Store, merge), launchers.py (Steam VDF/ACF, Epic manifests)
  uninstall/ methods.py, runner.py (completion detection), processes.py, leftovers.py, regops.py (backup/restore)
  dupes/     finder.py (large files, 3-stage duplicates)
  safety/    protect.py (policy), deleter.py (single delete gateway), oplog.py
  ui/        theme, icons, widgets (donut, treemap, cards), workers, dialogs, actions, pages/*
tests/       pytest suite
tools/       make_icon.py, screenshots.py (dev helper)
build.ps1, build.bat, requirements*.txt, assets/hito.ico
```

## Reused from the reference project

Starting point: [saksham-kumar1621/pc-storage-cleaner](https://github.com/saksham-kumar1621/pc-storage-cleaner) (MIT), a single 1,566-line tkinter script.

**Kept:**
- the cleanup location catalog, extended to per-profile browser caches and more launchers and apps;
- Recycle Bin size and emptying through `SHQueryRecycleBinW` / `SHEmptyRecycleBinW`;
- Firefox `cache2` handling, the DNS flush and hibernation through `powercfg`;
- the Nuitka onefile build approach.

**Changed for safety:**

| Reference behaviour | Here |
|---|---|
| Deleted Office "Recent"/"UnsavedFiles", Zoom "data", Recent Documents, the Windows Installer `$PatchCache$`, Prefetch | Removed: personal or recovery data with little or no space benefit |
| Cleared the Security and System event logs; stopped the print spooler | Removed |
| Deleted Windows.old and SoftwareDistribution directly | Handled only through Disk Cleanup, DISM `/StartComponentCleanup` and `Delete-DeliveryOptimizationCache` |
| Folder walks (`rglob`, `rmtree`) could follow junctions | Never follow links |
| Temp files of any age | Temp files newer than 24 h are kept |

## Known limitations / not supported

- **Sizes are logical sizes.** NTFS-compressed files, sparse files and cluster slack are not reflected, so totals can differ slightly from Explorer's "size on disk". Hard links are deduplicated inside `C:\Windows` by default; elsewhere only when enabled in Settings.
- **Without admin rights** some system folders cannot be read; they are listed as "unreadable". The used-space figure Windows reports also includes Volume Shadow Copies and file system metadata, which a folder scan cannot see.
- **Epic Games:** Epic has no public uninstall command, so Hito opens the launcher library and waits for the game's manifest to disappear. **Battle.net, EA, Ubisoft and GOG** games are uninstalled through their registry uninstallers. **Xbox / Game Pass** titles appear as Store packages.
- **Store app sizes** do not include shared framework packages. Some WindowsApps folders cannot be measured and show the estimate (`~`) or `-`.
- **Leftover detection** covers files, folders, shortcuts, uninstall entries, settings keys under the publisher or product name, Run entries and App Paths. It does **not** remove services, drivers, scheduled tasks, COM/file-association registrations or firewall rules. Those are shared and riskier and are better left to the uninstaller.
- **Uninstall completion** is detected through the app's registration disappearing. A few uninstallers finish only after a restart; the app is then reported as "still installed" with that hint.
- **Dry-run** does not apply to an app's own uninstaller (that is a separate program). The confirmation dialog says so.
- The DISM component cleanup can take many minutes and shows its output only when it finishes.
- The exe is not code-signed, and there is no auto-update.
- Tested on Windows 11 (build 26300). Windows 10 support is intended (it uses Segoe MDL2 icons when Fluent icons are missing) but has **not** been tested.

## License

MIT. Cleanup catalog ideas © Saksham Kumar (MIT), pc-storage-cleaner.
