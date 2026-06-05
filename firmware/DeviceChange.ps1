<#
.SYNOPSIS
    Clear the ESP-IDF VS Code extension's cached USB iSerial when swapping ESP32-S3 boards.

.DESCRIPTION
    The ESP-IDF VS Code extension persists the last-seen board's USB iSerial
    (the chip MAC for ESP32-S3 USB-JTAG) in:

        %APPDATA%\Code\User\workspaceStorage\<workspaceHash>\state.vscdb
            ItemTable, key 'espressif.esp-idf-extension'
            JSON field 'openocd.usbAdapterSerial'

    When a different board is plugged in, the extension still passes that
    stale serial to OpenOCD as 'adapter serial ...', which then fails with
    "No device matches the serial string" / "could not find or open device!".

    The value is NOT exposed in settings.json or the Settings UI, so a normal
    config search misses it. This script:

      1. Locates the workspaceStorage entry whose workspace.json 'folder' URI
         matches a target path (defaults to this script's directory).
      2. Stops VS Code if running so the SQLite DB is unlocked.
      3. Backs up state.vscdb with a timestamped suffix.
      4. Removes 'openocd.usbAdapterSerial' from the cached JSON via Python's
         sqlite3 (the only stdlib SQLite client guaranteed available here).

.PARAMETER WorkspacePath
    Workspace folder to match. Defaults to the directory containing this script.

.PARAMETER AllWorkspaces
    Patch every workspaceStorage entry, not just the matching one. Use only when
    you really want to wipe the cached serial across all VS Code workspaces.

.PARAMETER NoStopCode
    Skip killing running VS Code processes. The patch will fail if the DB is
    locked, but this is useful when VS Code is already closed.

.EXAMPLE
    .\DeviceChange.ps1
    Patches the workspace this script lives in (the Espress_dev project).

.EXAMPLE
    .\DeviceChange.ps1 -WorkspacePath 'C:\path\to\other\project'
    Patches a different workspace.

.NOTES
    Background: LearnedPatterns.md §5.10, ToDo.md 2026-05-20 entry.
#>

[CmdletBinding()]
param(
    [string] $WorkspacePath = $PSScriptRoot,
    [switch] $AllWorkspaces,
    [switch] $NoStopCode
)

$ErrorActionPreference = 'Stop'

function Find-WorkspaceEntries {
    param(
        [string] $TargetPath,
        [switch] $All
    )

    $root = Join-Path $env:APPDATA 'Code\User\workspaceStorage'
    if (-not (Test-Path $root)) {
        throw "workspaceStorage directory not found: $root"
    }

    $target = $null
    if (-not $All) {
        $resolved = Resolve-Path -LiteralPath $TargetPath -ErrorAction SilentlyContinue
        if (-not $resolved) {
            throw "WorkspacePath does not exist: $TargetPath"
        }
        $target = $resolved.Path.TrimEnd('\')
    }

    $results = @()
    Get-ChildItem -Path $root -Directory | ForEach-Object {
        $workspaceJson = Join-Path $_.FullName 'workspace.json'
        if (-not (Test-Path $workspaceJson)) { return }

        try {
            $wj = Get-Content -Raw -LiteralPath $workspaceJson | ConvertFrom-Json
        } catch { return }

        $folderUri = $wj.folder
        if (-not $folderUri) { return }

        if ($All) {
            $results += [pscustomobject]@{
                Hash   = $_.Name
                Path   = $_.FullName
                Folder = $folderUri
            }
            return
        }

        try {
            $uri = [Uri] $folderUri
            if (-not $uri.IsFile) { return }
            $candidate = [Uri]::UnescapeDataString($uri.LocalPath).TrimStart('/')
            $candidateResolved = Resolve-Path -LiteralPath $candidate -ErrorAction SilentlyContinue
        } catch { return }

        if (-not $candidateResolved) { return }

        if ($candidateResolved.Path.TrimEnd('\') -ieq $target) {
            $results += [pscustomobject]@{
                Hash   = $_.Name
                Path   = $_.FullName
                Folder = $folderUri
            }
        }
    }
    return $results
}

Write-Host "[*] Target workspace : $WorkspacePath"
$entries = Find-WorkspaceEntries -TargetPath $WorkspacePath -All:$AllWorkspaces
if (-not $entries -or $entries.Count -eq 0) {
    throw "No matching workspaceStorage entry. Re-run with -AllWorkspaces to see every cached workspace."
}

Write-Host "[*] Matched $($entries.Count) entry(ies):"
foreach ($e in $entries) {
    Write-Host "      $($e.Hash)  <-  $($e.Folder)"
}

if (-not $NoStopCode) {
    $code = Get-Process -Name 'Code' -ErrorAction SilentlyContinue
    if ($code) {
        Write-Host "[*] Stopping VS Code ($($code.Count) process(es))..."
        $code | Stop-Process -Force
        Start-Sleep -Seconds 1
    }
}

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    throw "python not found on PATH. Install Python or add it to PATH, then re-run."
}

$pyScript = @'
import sqlite3, json, sys

db_path = sys.argv[1]
con = sqlite3.connect(db_path)
cur = con.cursor()
row = cur.execute(
    "SELECT value FROM ItemTable WHERE key='espressif.esp-idf-extension'"
).fetchone()

if row is None:
    print("[skip] key 'espressif.esp-idf-extension' absent from ItemTable")
    sys.exit(0)

value = row[0]
if isinstance(value, bytes):
    value = value.decode('utf-8')

data = json.loads(value)
cached = data.get('openocd.usbAdapterSerial')
if cached is None:
    print("[skip] 'openocd.usbAdapterSerial' not set; nothing to clear")
    sys.exit(0)

print(f"[before] openocd.usbAdapterSerial = {cached}")
data.pop('openocd.usbAdapterSerial', None)
cur.execute(
    "UPDATE ItemTable SET value=? WHERE key='espressif.esp-idf-extension'",
    (json.dumps(data),),
)
con.commit()
print("[ok] removed 'openocd.usbAdapterSerial'")
'@

$tempPy = Join-Path ([System.IO.Path]::GetTempPath()) ("devicechange_" + [Guid]::NewGuid().ToString('N') + ".py")
Set-Content -LiteralPath $tempPy -Value $pyScript -Encoding utf8

try {
    foreach ($entry in $entries) {
        $db = Join-Path $entry.Path 'state.vscdb'
        if (-not (Test-Path $db)) {
            Write-Warning "[$($entry.Hash)] state.vscdb missing - skipping"
            continue
        }

        $backup = "$db.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Copy-Item -LiteralPath $db -Destination $backup
        Write-Host "[*] Backup created: $backup"

        Write-Host "[*] Patching: $db"
        & $python $tempPy $db
        if ($LASTEXITCODE -ne 0) {
            throw "python patch failed for $db (exit $LASTEXITCODE)"
        }
    }
} finally {
    Remove-Item -LiteralPath $tempPy -Force -ErrorAction SilentlyContinue
}

Write-Host "[done] Restart VS Code; the ESP-IDF extension will re-detect the connected board on next OpenOCD launch."