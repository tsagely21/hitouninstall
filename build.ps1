<#
  Reproducible build of dist\HitoUninstall.exe (portable, single file, no console window).

  .\build.ps1                 # venv + deps + tests + exe
  .\build.ps1 -SkipTests      # skip pytest
  .\build.ps1 -Venv C:\venvs\hito

  Needs Python 3.12 (py launcher or python on PATH). Nuitka downloads its own
  MinGW64 C compiler on first use if Visual Studio Build Tools are not installed.
#>
param(
    [string]$Venv = "$env:LOCALAPPDATA\HitoUninstall-build\venv",
    # Nuitka's intermediate files go here (kept out of the repo / OneDrive); only the exe is copied to .\dist
    [string]$BuildDir = "$env:LOCALAPPDATA\HitoUninstall-build\out",
    [switch]$SkipTests
)
# 'Continue': Windows PowerShell 5.1 turns a native tool's stderr (Nuitka/pip progress) into error
# records, which 'Stop' would treat as fatal. Failures are caught through $LASTEXITCODE instead.
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot

if (-not (Test-Path "$Venv\Scripts\python.exe")) {
    Write-Host "Creating build environment in $Venv"
    if (Get-Command py -ErrorAction SilentlyContinue) { py -3.12 -m venv $Venv } else { python -m venv $Venv }
}
$py = "$Venv\Scripts\python.exe"

& $py -m pip install --disable-pip-version-check -q -r requirements.txt -r requirements-build.txt
if ($LASTEXITCODE) { throw "pip install failed" }

if (-not (Test-Path assets\hito.ico)) { & $py tools\make_icon.py }

if (-not $SkipTests) {
    & $py -m pyflakes hito tests tools
    if ($LASTEXITCODE) { throw "lint failed" }
    & $py -m pytest
    if ($LASTEXITCODE) { throw "tests failed" }
}

$version = (& $py -c "import hito; print(hito.__version__)").Trim()
$fileVersion = "$version.0"
Write-Host "Building Hito Uninstall $version"

& $py -m nuitka `
    --onefile `
    --mingw64 `
    --assume-yes-for-downloads `
    --windows-console-mode=disable `
    --enable-plugin=pyside6 `
    --noinclude-qt-translations `
    --nofollow-import-to=tests,tools,pytest,_pytest `
    --include-data-files=assets/hito.ico=assets/hito.ico `
    --windows-icon-from-ico=assets/hito.ico `
    --company-name="Hito" `
    --product-name="Hito Uninstall" `
    --file-description="Hito Uninstall - storage analyzer, smart cleaner and deep uninstaller" `
    --copyright="MIT License" `
    --file-version=$fileVersion `
    --product-version=$fileVersion `
    --onefile-tempdir-spec="{CACHE_DIR}/HitoUninstall/$version" `
    --output-dir=$BuildDir `
    --output-filename=HitoUninstall.exe `
    hito_main.py
if ($LASTEXITCODE) { throw "Nuitka build failed" }

New-Item -ItemType Directory -Force dist | Out-Null
Copy-Item "$BuildDir\HitoUninstall.exe" dist\HitoUninstall.exe -Force
$exe = Get-Item dist\HitoUninstall.exe
Write-Host ("Built {0} ({1:N1} MB)" -f $exe.FullName, ($exe.Length / 1MB))
