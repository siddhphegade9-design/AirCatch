<#
Builds AirCatch for Windows. Run from the project folder, in the virtual environment:

    .venv\Scripts\Activate.ps1
    pip install -r requirements.txt pyinstaller
    powershell -ExecutionPolicy Bypass -File packaging\build.ps1

What it makes (all in dist\):
    AirCatch\                            the program folder (AirCatch.exe and everything it needs)
    AirCatch-<version>-windows-portable.zip   that folder zipped: unzip anywhere and run, no install
    installer\AirCatch-Setup-<version>.exe    the installer (needs Inno Setup 6)
    self-test.json                       what the packaged program could do when it was started
    SHA256SUMS.txt                       checksums of the two downloads

Options:
    -SkipInstaller     build the folder and the zip only
    -SkipSelfTest      do not start the packaged program to check it
    -SkipBuild         keep the dist\AirCatch folder from the last run (already built and tested) and
                       only make the zip, the installer and the checksums again
    -InstallInnoSetup  install Inno Setup with Chocolatey if it is missing (used by the GitHub build)
#>
param(
    [switch]$SkipInstaller,
    [switch]$SkipSelfTest,
    [switch]$SkipBuild,
    [switch]$InstallInnoSetup
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "`nBUILD FAILED: $text" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- version and tools
$match = Select-String -Path "src\aircatch\__init__.py" -Pattern '__version__ = "([^"]+)"'
if (-not $match) { Fail "no __version__ in src\aircatch\__init__.py" }
$version = $match.Matches[0].Groups[1].Value
Step "Building AirCatch $version"

$pyVersion = (& python -c "import sys; print('%d.%d' % sys.version_info[:2])")
if ($LASTEXITCODE -ne 0) { Fail "python was not found. Activate the virtual environment first: .venv\Scripts\Activate.ps1" }
if ($pyVersion -ne "3.11") { Write-Host "Note: this is Python $pyVersion. AirCatch is developed and tested on 3.11." -ForegroundColor Yellow }

# (asked this way so that nothing is printed on failure: Windows PowerShell 5 treats error output as an error)
& python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('PyInstaller') else 1)"
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller is not installed. Run:  pip install pyinstaller" }

# PySide6 6.12.0 crashes AirCatch on Python 3.11 (see docs\milestone-11.md). Never package it.
$pyside = (& python -c "import PySide6; print(PySide6.__version__)")
if ($pyside -like "6.12*") { Fail "PySide6 $pyside is installed and it crashes AirCatch. Run:  pip install `"PySide6>=6.7,<6.12`"" }
Write-Host "Python $pyVersion, PySide6 $pyside"

if (-not (Test-Path "packaging\aircatch.ico")) {
    Step "Making the icon"
    & python packaging\make_icon.py
    if ($LASTEXITCODE -ne 0) { Fail "could not make packaging\aircatch.ico" }
}

# ---------------------------------------------------------------- the program folder
$exe = "dist\AirCatch\AirCatch.exe"
if ($SkipBuild) {
    if (-not (Test-Path $exe)) { Fail "-SkipBuild needs dist\AirCatch from an earlier run, and it is not there" }
    Step "Keeping the dist\AirCatch folder from the last run"
    $SkipSelfTest = $true
} else {
    Step "Packaging with PyInstaller (a few minutes)"
    Remove-Item -Recurse -Force "dist\AirCatch" -ErrorAction SilentlyContinue
    & python -m PyInstaller packaging\aircatch.spec --noconfirm --distpath dist --workpath build
    if ($LASTEXITCODE -ne 0) { Fail "PyInstaller failed (see the messages above)" }
    if (-not (Test-Path $exe)) { Fail "$exe was not produced" }

    # The Microsoft C++ runtime: present on this PC, but not on every clean Windows. Ship it with the program.
    $internal = "dist\AirCatch\_internal"
    foreach ($dll in @("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "concrt140.dll", "vcomp140.dll")) {
        if (-not (Test-Path (Join-Path $internal $dll))) {
            $system = Join-Path $env:WINDIR "System32\$dll"
            if (Test-Path $system) {
                Copy-Item $system $internal
                Write-Host "added $dll"
            } else {
                Write-Host "note: $dll is not on this PC, so it is not included" -ForegroundColor Yellow
            }
        }
    }

    foreach ($needed in @("_internal\aircatch\ui\main.qml", "_internal\aircatch\ui\assets\aircatch-icon.png")) {
        if (-not (Test-Path (Join-Path "dist\AirCatch" $needed))) { Fail "the package is missing $needed" }
    }
    $models = @(Get-ChildItem -Recurse -Filter "*.tflite" "dist\AirCatch\_internal\mediapipe" -ErrorAction SilentlyContinue)
    if ($models.Count -lt 2) { Fail "the hand-tracking models (mediapipe\...\*.tflite) are missing from the package" }
    $sizeMb = [math]::Round(((Get-ChildItem -Recurse "dist\AirCatch" | Measure-Object Length -Sum).Sum / 1MB))
    Write-Host "dist\AirCatch: $sizeMb MB, $($models.Count) hand-tracking model files"
}

# ---------------------------------------------------------------- does the packaged program work?
if (-not $SkipSelfTest) {
    Step "Starting the packaged program to test it (window, network, hand tracker, pairing, a transfer)"
    Write-Host "Windows Firewall may ask about AirCatch here. Either answer is fine for the test."
    $report = Join-Path $root "dist\self-test.json"
    Remove-Item $report -ErrorAction SilentlyContinue
    $proc = Start-Process -FilePath $exe -ArgumentList @("--self-test", "`"$report`"") -PassThru
    if (-not $proc.WaitForExit(180000)) {
        $proc.Kill()
        Fail "the packaged program did not finish its self-test within 3 minutes"
    }
    if (-not (Test-Path $report)) { Fail "the packaged program wrote no self-test report (exit code $($proc.ExitCode)). Look in %LOCALAPPDATA%\AirCatch\crash.log" }
    $result = Get-Content $report -Raw | ConvertFrom-Json
    foreach ($check in $result.checks.PSObject.Properties) {
        $mark = if ($check.Value.ok) { "ok  " } else { "FAIL" }
        Write-Host ("  {0} {1,-18} {2}" -f $mark, $check.Name, $check.Value.detail)
    }
    if (-not $result.passed) { Fail "the packaged program failed its self-test (dist\self-test.json)" }
    Write-Host "Self-test passed." -ForegroundColor Green
    for ($i = 0; $i -lt 20; $i++) {          # let the tested program finish closing before its files are packed
        $left = @(Get-Process -Name "AirCatch" -ErrorAction SilentlyContinue | Where-Object { $_.Id -eq $proc.Id })
        if (-not $left.Count) { break }
        Start-Sleep -Milliseconds 500
    }
}

# ---------------------------------------------------------------- portable zip
Step "Making the portable zip"
$zip = "dist\AirCatch-$version-windows-portable.zip"
# Not Compress-Archive: it refuses any file another program has open, even just for reading (Windows
# Defender scanning the files the self-test just ran, or the tested program still closing). This
# opens each file in a mode that lets others keep it open, and tries again for a while if a file is
# truly locked.
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
function New-ZipShared([string]$folder, [string]$zipPath) {
    $base = Split-Path -Parent $folder
    $out = [System.IO.File]::Open($zipPath, [System.IO.FileMode]::Create, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    try {
        $archive = New-Object System.IO.Compression.ZipArchive($out, [System.IO.Compression.ZipArchiveMode]::Create)
        try {
            foreach ($file in Get-ChildItem -LiteralPath $folder -Recurse -File) {
                $name = $file.FullName.Substring($base.Length).TrimStart('\', '/').Replace('\', '/')
                $entry = $archive.CreateEntry($name, [System.IO.Compression.CompressionLevel]::Optimal)
                $entry.LastWriteTime = $file.LastWriteTime
                $share = [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete
                $src = [System.IO.File]::Open($file.FullName, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, $share)
                try {
                    $dst = $entry.Open()
                    try { $src.CopyTo($dst) } finally { $dst.Dispose() }
                } finally { $src.Dispose() }
            }
        } finally { $archive.Dispose() }
    } finally { $out.Dispose() }
}
$zipFull = Join-Path $root $zip
$folderFull = Join-Path $root "dist\AirCatch"
$made = $false
for ($try = 1; $try -le 8 -and -not $made; $try++) {
    try {
        if (Test-Path $zipFull) { Remove-Item $zipFull -Force }
        New-ZipShared $folderFull $zipFull
        $made = $true
    } catch {
        $why = $_.Exception.Message
        if ($_.Exception.InnerException) { $why = $_.Exception.InnerException.Message }
        Write-Host "  a file is in use, waiting 5 s (try $try of 8): $why" -ForegroundColor Yellow
        Start-Sleep -Seconds 5
    }
}
if (-not $made) {
    Remove-Item $zipFull -Force -ErrorAction SilentlyContinue
    $running = @(Get-Process -Name "AirCatch" -ErrorAction SilentlyContinue)
    if ($running.Count) { Fail "AirCatch is still running ($($running.Count) window(s)). Close it and run:  packaging\build.ps1 -SkipBuild" }
    Fail "files in dist\AirCatch stayed locked by another program (often an antivirus scan). Wait a minute and run:  packaging\build.ps1 -SkipBuild"
}
Write-Host "$zip  ($([math]::Round((Get-Item $zip).Length / 1MB)) MB)"

# ---------------------------------------------------------------- installer
$setup = $null
if (-not $SkipInstaller) {
    Step "Making the installer with Inno Setup"
    $iscc = (Get-Command "ISCC.exe" -ErrorAction SilentlyContinue).Source
    if (-not $iscc) {
        foreach ($candidate in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")) {
            if (Test-Path $candidate) { $iscc = $candidate; break }
        }
    }
    if (-not $iscc -and $InstallInnoSetup) {
        & choco install innosetup -y --no-progress
        $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    }
    if (-not $iscc -or -not (Test-Path $iscc)) {
        Write-Host "Inno Setup 6 was not found, so no installer was made." -ForegroundColor Yellow
        Write-Host "Install it once with:   winget install JRSoftware.InnoSetup"
        Write-Host "(or from https://jrsoftware.org/isdl.php), then run this script again."
    } else {
        & $iscc "/DAppVersion=$version" "/Qp" "packaging\installer.iss"
        if ($LASTEXITCODE -ne 0) { Fail "Inno Setup failed (see the messages above)" }
        $setup = "dist\installer\AirCatch-Setup-$version.exe"
        if (-not (Test-Path $setup)) { Fail "$setup was not produced" }
        Write-Host "$setup  ($([math]::Round((Get-Item $setup).Length / 1MB)) MB)"
    }
}

# ---------------------------------------------------------------- checksums
Step "Checksums"
$lines = @()
foreach ($file in @($setup, $zip) | Where-Object { $_ }) {
    $hash = (Get-FileHash $file -Algorithm SHA256).Hash.ToLower()
    $lines += "$hash  $(Split-Path -Leaf $file)"
}
$lines | Set-Content -Encoding ascii "dist\SHA256SUMS.txt"
$lines | ForEach-Object { Write-Host $_ }

Write-Host "`nDone. AirCatch $version is in the dist folder." -ForegroundColor Green
if ($setup) { Write-Host "Installer: $setup" }
Write-Host "Portable:  $zip"
