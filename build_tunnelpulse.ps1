# ============================================================================
# TunnelPulse build script
# Run it through RUN_BUILD.bat (double-click). The .bat bypasses PowerShell's
# execution policy for this one script and keeps the window open at the end.
#
# Every run writes a full transcript to TunnelPulse-BuildLog-<time>.txt.
#
# Build choices:
#   --onedir      A plain folder with no self-extraction step at launch, which
#                 antivirus heuristics flag far less than --onefile. The folder
#                 is zipped at the end so it ships as a single file.
#   no --uac-admin  The app runs as a standard user and asks for admin rights
#                 only when Connect is pressed, so the exe must not force a UAC
#                 prompt on every launch.
#
# Output (all in dist\):
#   TunnelPulse\            the app folder
#   TunnelPulse-Portable.zip that folder, zipped
#   TunnelPulse-Setup.exe    the installer (needs Inno Setup 6.6+, installed
#                            automatically with winget if missing)
#
# The version comes from APP_VERSION in tunnel_pulse_core.py.
# ============================================================================

param(
    # For automated builds (GitHub Actions): no prompts, no Explorer window,
    # and a missing Inno Setup is an error instead of a warning.
    [switch]$NonInteractive
)

$APP_NAME   = "TunnelPulse"
$APP_AUTHOR = "Zain Raza"          # change to your company name if/when this becomes a company build
$VENV_DIR   = "venv"
$ENTRY      = "tunnel_pulse_gui.py"
$CORE_FILE  = "tunnel_pulse_core.py"
$OUT_NAME   = "TunnelPulse"
$DIST_DIR   = "dist"
$BUILD_DIR  = "build"
$ICON_FILE  = "assets\icon.ico"   # app and installer icon
$BIN_DIR    = "bin"               # optional - put wireguard.exe/wg.exe/wintun.dll here to bundle them

# ---- build log ----
$ErrorActionPreference = "Stop"   # any unexpected/uncaught error bubbles to the catch block below instead of the window just vanishing
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"   # pip's "new version available" notice goes to stderr, which Stop turns into an error
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
Set-Location -Path $ScriptDir   # all paths below are relative to the script's folder
$LogFile   = Join-Path $ScriptDir ("TunnelPulse-BuildLog-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".txt")

try {
    Start-Transcript -Path $LogFile -Append -ErrorAction Stop | Out-Null
} catch {
    Write-Host "Could not start log file ($($_.Exception.Message)) - continuing without one." -ForegroundColor Yellow
}

function Log($Msg, $Color = "Cyan") {
    $ts = Get-Date -Format "HH:mm:ss"
    Write-Host "[$ts] $Msg" -ForegroundColor $Color
}

function Find-InnoSetup {
    # Returns @{ Path; Version } for an Inno Setup compiler new enough for the
    # dark wizard style (6.6+), or $null. $script:InnoSeen lists what was found,
    # for the error message.
    $candidates = @()
    $regVersion = @{}
    if ($env:TUNNELPULSE_ISCC) { $candidates += $env:TUNNELPULSE_ISCC }
    # Where Windows says Inno Setup is installed (machine-wide or per-user).
    $uninstallKeys = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup *_is1",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup *_is1",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup *_is1"
    )
    foreach ($k in $uninstallKeys) {
        Get-ItemProperty -Path $k -ErrorAction SilentlyContinue | ForEach-Object {
            if ($_.InstallLocation) {
                $exe = Join-Path $_.InstallLocation "ISCC.exe"
                $candidates += $exe
                if ($_.DisplayVersion) { $regVersion[$exe.ToLower()] = $_.DisplayVersion }
            }
        }
    }
    # Usual folders, for any major version.
    foreach ($root in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, "$env:LOCALAPPDATA\Programs")) {
        if ($root) {
            $candidates += Get-ChildItem -Path $root -Filter "Inno Setup *" -Directory -ErrorAction SilentlyContinue |
                ForEach-Object { Join-Path $_.FullName "ISCC.exe" }
        }
    }
    # Last: whatever is on PATH. Chocolatey puts a small launcher ("shim") there
    # whose version number isn't Inno Setup's, so shims are skipped.
    $onPath = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($onPath -and $onPath.Source -notmatch '\\chocolatey\\bin\\') { $candidates += $onPath.Source }

    $script:InnoSeen = @()
    $best = $null
    $unknown = $null
    foreach ($c in ($candidates | Select-Object -Unique)) {
        if (-not $c -or -not (Test-Path $c)) { continue }
        # ISCC.exe itself carries no version number (it reads 0.0.0.0), so ask
        # the compiler DLL next to it, then the version Windows recorded.
        $v = $null
        $sources = @($c, (Join-Path (Split-Path $c) "ISCmplr.dll"))
        foreach ($f in $sources) {
            if ($v -or -not (Test-Path $f)) { continue }
            $info = (Get-Item $f).VersionInfo
            foreach ($text in @($info.ProductVersion, $info.FileVersion)) {
                if (-not $v -and $text -match '^(\d+)\.(\d+)\.(\d+)' -and [int]$Matches[1] -gt 0) {
                    $v = [version]"$($Matches[1]).$($Matches[2]).$($Matches[3])"
                }
            }
        }
        $r = $regVersion[$c.ToLower()]
        if (-not $v -and $r -match '^(\d+)\.(\d+)\.(\d+)') { $v = [version]"$($Matches[1]).$($Matches[2]).$($Matches[3])" }
        $script:InnoSeen += "$c (version $(if ($v) { $v } else { 'unknown' }))"
        if ($v) {
            if ($v -ge [version]"6.6.0" -and (-not $best -or $v -gt $best.Version)) { $best = @{ Path = $c; Version = $v } }
        } elseif (-not $unknown) {
            $unknown = @{ Path = $c; Version = "unknown" }
        }
    }
    # A copy whose version can't be read is still tried: the installer script
    # itself stops with a clear message if the compiler is older than 6.6.
    if (-not $best) { $best = $unknown }
    return $best
}

function Invoke-Tool([string]$Exe, [string[]]$ToolArgs) {
    # Runs a native program, shows its output as it goes, and returns the
    # output lines. Windows PowerShell turns a native program's stderr into
    # terminating errors under "Stop", so relax that for the call.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $lines = & $Exe @ToolArgs 2>&1 | ForEach-Object { $line = "$_"; Write-Host $line; $line }
    } finally {
        $ErrorActionPreference = $prev
    }
    return , @($lines)
}

function Tail($Lines, [int]$Count = 8) {
    (@($Lines) | Where-Object { $_ -and $_.Trim() } | Select-Object -Last $Count) -join " | "
}

function Fail($Msg) {
    # throw (not exit) so this is always caught by the try/catch below,
    # which logs it, saves the transcript, and keeps the window open.
    throw $Msg
}

$script:BuildFailed = $false

try {

    # -- Guard: Windows only
    # tunnel_pulse_core.py uses ctypes.windll, netsh and wireguard.exe.
    if ($PSVersionTable.PSVersion.Major -ge 6 -and -not $IsWindows) {
        Fail "TunnelPulse is Windows-only (ctypes.windll, netsh, wireguard.exe). Build on Windows."
    }

    # -- Guard: must not run inside an already-activated venv
    if ($env:VIRTUAL_ENV) {
        Log "Deactivating active venv before build..." "Yellow"
        & deactivate 2>$null
    }

    Log "=== $APP_NAME Build System ===" "Magenta"
    Log "Log file: $LogFile" "DarkGray"

    # -- STEP 1: Confirm both source files are actually here
    Log "[1/11] Checking source files..."

    if (-not (Test-Path $ENTRY)) {
        Fail "$ENTRY not found. Run this script from the folder containing both .py files."
    }
    if (-not (Test-Path $CORE_FILE)) {
        Fail "$CORE_FILE not found next to $ENTRY - both files are required together (the GUI imports the core module directly)."
    }
    Log "  Found $ENTRY + $CORE_FILE" "Green"

    $verMatch = Select-String -Path $CORE_FILE -Pattern '^APP_VERSION\s*=\s*"(\d+\.\d+\.\d+)"' | Select-Object -First 1
    if (-not $verMatch) { Fail "APP_VERSION = ""x.y.z"" not found in $CORE_FILE" }
    $APP_VER = $verMatch.Matches[0].Groups[1].Value
    Log "  Version: $APP_VER" "Green"

    # -- STEP 2: Find Python 3.11+ 64-bit
    Log "[2/11] Finding Python..."

    try {
        $cmd = Get-Command python.exe -ErrorAction Stop
        $PY = $cmd.Source
        Log "  Found: $PY" "Green"

        $probeFile = Join-Path $env:TEMP "tunnelpulse_python_probe.py"

        @"
import sys
import struct
print(f"{sys.version_info.major}{sys.version_info.minor}")
print(struct.calcsize("P") * 8)
"@ | Set-Content -Path $probeFile -Encoding ASCII

        $probeOut = & $PY $probeFile 2>&1
        Remove-Item $probeFile -Force -ErrorAction SilentlyContinue

        $probeOut = @($probeOut) | ForEach-Object { "$_".Trim() } | Where-Object { $_ -ne "" }

        if ($probeOut.Count -lt 2) {
            Fail "Python probe returned blank output."
        }

        $ver  = $probeOut[0]
        $bits = $probeOut[1]

        Log "  Probe version='$ver' bits='$bits'" "Yellow"

        if ($ver -notmatch '^\d+$') {
            Fail "Python version probe returned unexpected output: '$ver'"
        }

        $verNum = [int]$ver
        if ($verNum -lt 311 -or $bits -ne "64") {
            Fail "Python must be 3.11+ and 64-bit. Found v$ver, $bits-bit."
        }

        Log "  Python OK: v$ver, $bits-bit" "Green"
    }
    catch {
        Log "  Raw error: $($_.Exception.Message)" "Red"
        Fail "Python check failed."
    }

    $VER_CODE = "import sys; print('ok') if sys.version_info >= (3, 11) else None"

    # -- STEP 3: Virtual environment
    Log "[3/11] Virtual environment..."

    $VENV_PY  = "$VENV_DIR\Scripts\python.exe"
    $VENV_PIP = "$VENV_DIR\Scripts\pip.exe"

    if (Test-Path $VENV_PY) {
        $chk = & $VENV_PY -c $VER_CODE 2>$null
        if ($chk) {
            Log "  Existing venv OK" "Green"
        } else {
            Log "  Venv corrupt, recreating..." "Yellow"
            Remove-Item -Recurse -Force $VENV_DIR -ErrorAction SilentlyContinue
        }
    }

    if (-not (Test-Path $VENV_PY)) {
        Log "  Creating venv..."
        $venvOut = & $PY -m venv $VENV_DIR 2>&1
        if ($LASTEXITCODE -ne 0) {
            Log "$venvOut" "Red"
            Fail "venv creation failed"
        }
    }

    # -- STEP 4: Upgrade pip
    Log "[4/11] Upgrading pip..."
    $pipUpOut = & $VENV_PY -m pip install --upgrade pip setuptools wheel --quiet 2>&1
    if ($LASTEXITCODE -ne 0) {
        Log "$pipUpOut" "Yellow"
        Log "  pip upgrade had warnings (non-fatal)" "Yellow"
    }

    # -- STEP 5: Install packages
    # Only 4 third-party imports exist across both files: customtkinter,
    # requests, psutil, pydantic. Everything else is stdlib.
    Log "[5/11] Installing packages..."

    if (Test-Path "requirements.txt") {
        $reqOut = & $VENV_PIP install -r requirements.txt --quiet 2>&1
        if ($LASTEXITCODE -ne 0) {
            Log "$reqOut" "Red"
            Fail "pip install -r requirements.txt failed"
        }
        Log "  requirements.txt installed" "Green"
    } else {
        $pkgs = "customtkinter>=5.2.0", "requests>=2.31.0", "psutil>=5.9.0",
                "pydantic>=2.4.0", "pyinstaller>=6.3.0"
        $pkgOut = & $VENV_PIP install @pkgs --quiet 2>&1
        if ($LASTEXITCODE -ne 0) {
            Log "$pkgOut" "Red"
            Fail "package install failed"
        }
    }

    # The core module uses pydantic v2 APIs (field_validator,
    # model_validate_json); upgrade if v1 got installed.
    $pydChk = & $VENV_PY -c "import pydantic; print('ok' if hasattr(pydantic, 'field_validator') else 'v1')" 2>$null
    if ($pydChk -ne "ok") {
        Log "  pydantic v1 (or broken) detected - forcing upgrade to v2..." "Yellow"
        & $VENV_PIP install --upgrade --force-reinstall "pydantic>=2.4.0" --quiet
    }

    # -- STEP 6: Verify imports
    Log "[6/11] Verifying imports..."

    $mods = @{
        "customtkinter" = "customtkinter>=5.2.0"
        "requests"      = "requests>=2.31.0"
        "psutil"        = "psutil>=5.9.0"
        "pydantic"      = "pydantic>=2.4.0"
    }

    foreach ($mod in $mods.Keys) {
        $res = & $VENV_PY -c "import $mod; print('ok')" 2>$null
        if ($res -eq "ok") {
            Log "  OK: $mod" "Green"
        } else {
            $pkg = $mods[$mod]
            Log "  Missing $mod, installing $pkg..." "Yellow"
            & $VENV_PIP install --force-reinstall $pkg --quiet 2>$null
            $res2 = & $VENV_PY -c "import $mod; print('ok')" 2>$null
            if ($res2 -eq "ok") { Log "    Repaired" "Green" }
            else                 { Log "    Still missing (will likely fail at build/runtime)" "Yellow" }
        }
    }

    # Make sure the core module imports before spending time on PyInstaller.
    $importChk = & $VENV_PY -c "import ast,sys; sys.path.insert(0,'.'); import tunnel_pulse_core" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Log "  $importChk" "Red"
        Fail "tunnel_pulse_core.py failed to import cleanly - fix this before building (PyInstaller would just fail the same way)."
    }
    Log "  tunnel_pulse_core.py imports cleanly" "Green"

    # -- STEP 7: version_info.txt
    Log "[7/11] Writing version_info.txt..."

    $p = $APP_VER.Split(".")
    while ($p.Count -lt 4) { $p += "0" }
    $fv = "(" + $p[0] + ", " + $p[1] + ", " + $p[2] + ", " + $p[3] + ")"
    $yr = (Get-Date).Year

    $vi  = "VSVersionInfo("                                               + "`n"
    $vi += "  ffi=FixedFileInfo("                                         + "`n"
    $vi += "    filevers=" + $fv + ","                                    + "`n"
    $vi += "    prodvers=" + $fv + ","                                    + "`n"
    $vi += "    mask=0x3f, flags=0x0, OS=0x40004,"                       + "`n"
    $vi += "    fileType=0x1, subtype=0x0, date=(0, 0)"                  + "`n"
    $vi += "  ),"                                                         + "`n"
    $vi += "  kids=["                                                     + "`n"
    $vi += "    StringFileInfo(["                                         + "`n"
    $vi += "      StringTable(u'040904B0', ["                             + "`n"
    $vi += "        StringStruct(u'CompanyName',     u'" + $APP_AUTHOR + "'),"  + "`n"
    $vi += "        StringStruct(u'FileDescription', u'" + $APP_NAME + " - Self-Healing WireGuard VPN Client')," + "`n"
    $vi += "        StringStruct(u'FileVersion',     u'" + $APP_VER + "'),"   + "`n"
    $vi += "        StringStruct(u'ProductName',     u'" + $APP_NAME + "'),"  + "`n"
    $vi += "        StringStruct(u'LegalCopyright',  u'" + $yr + " " + $APP_AUTHOR + "')," + "`n"
    $vi += "        StringStruct(u'OriginalFilename',u'" + $OUT_NAME + ".exe')])" + "`n"
    $vi += "    ]),"                                                      + "`n"
    $vi += "    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])"  + "`n"
    $vi += "  ]"                                                          + "`n"
    $vi += ")"

    [System.IO.File]::WriteAllText("version_info.txt", $vi, [System.Text.Encoding]::UTF8)
    Log "  Written" "Green"

    # -- STEP 8: Clean & build
    Log "[8/11] Building (onedir, unsigned-friendly)..."

    foreach ($d in @($DIST_DIR, $BUILD_DIR)) {
        if (Test-Path $d) { Remove-Item -Recurse -Force $d -ErrorAction SilentlyContinue }
    }
    Get-ChildItem -Filter "$OUT_NAME.spec" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue

    $pyi = "$VENV_DIR\Scripts\pyinstaller.exe"
    if (-not (Test-Path $pyi)) { $pyi = "pyinstaller" }

    # Optional extras - bundled only if actually present, never required.
    $iconArgs = @()
    if (Test-Path $ICON_FILE) {
        Log "  Using $ICON_FILE as the app icon" "Green"
        # --add-data puts a copy where the window can load it (resource_dir()\assets).
        $iconArgs = @("--icon", $ICON_FILE, "--add-data", "$ICON_FILE;assets")
    } else {
        Log "  No $ICON_FILE found - building with the default icon" "Yellow"
    }

    $dataArgs = @()
    if (Test-Path $BIN_DIR) {
        Log "  Found $BIN_DIR\ - bundling wireguard.exe/wg.exe/wintun.dll for zero-install runs" "Green"
        $dataArgs = @("--add-data", "$BIN_DIR;bin")
    } else {
        Log "  No $BIN_DIR\ found - built app will rely on WireGuard for Windows being installed on the target PC" "Yellow"
    }

    $pyiArgs = @(
        "--noconfirm", "--clean", "--windowed",
        "--name",          $OUT_NAME,
        "--version-file",  "version_info.txt",
        "--distpath",      $DIST_DIR,
        "--workpath",      $BUILD_DIR,
        "--collect-all",   "customtkinter",
        "--collect-all",   "pydantic",
        "--hidden-import", "darkdetect",
        "--hidden-import", "packaging",
        "--hidden-import", "pydantic_core",
        "--hidden-import", "requests",
        "--hidden-import", "urllib3",
        "--hidden-import", "certifi",
        "--hidden-import", "charset_normalizer",
        "--hidden-import", "idna",
        "--hidden-import", "psutil",
        "--exclude-module", "matplotlib",
        "--exclude-module", "numpy",
        "--exclude-module", "scipy",
        "--exclude-module", "pandas",
        "--noupx"
    ) + $iconArgs + $dataArgs + @($ENTRY)

    $pyiOut = Invoke-Tool $pyi $pyiArgs
    if ($LASTEXITCODE -ne 0) { Fail "PyInstaller exited with code $LASTEXITCODE. Last output: $(Tail $pyiOut)" }

    # -- STEP 9: Verify output
    Log "[9/11] Verifying output..."

    $appDir = Join-Path $DIST_DIR $OUT_NAME
    $exe = Join-Path $appDir "$OUT_NAME.exe"
    if (-not (Test-Path $exe)) { Fail "EXE not found: $exe" }

    $folderSizeMB = [math]::Round(((Get-ChildItem $appDir -Recurse | Measure-Object Length -Sum).Sum) / 1MB, 1)
    Log "  App folder size: $folderSizeMB MB" "Green"

    $magic = [System.IO.File]::ReadAllBytes($exe)[0..1]
    if ($magic[0] -eq 0x4D -and $magic[1] -eq 0x5A) {
        Log "  PE header: valid" "Green"
    } else {
        Log "  WARNING: PE header unexpected" "Yellow"
    }

    # -- STEP 10: Zip the app folder and clean up
    Log "[10/11] Packaging + cleanup..."

    # Fixed file names, so the README can link to the newest release's files directly.
    $zipName = Join-Path $DIST_DIR "$OUT_NAME-Portable.zip"
    if (Test-Path $zipName) { Remove-Item -Force $zipName }
    Compress-Archive -Path $appDir -DestinationPath $zipName -Force
    Log "  Zipped: $zipName" "Green"

    # Remove build clutter; keep venv (for the next rebuild), dist\ and the zip.
    Remove-Item -Recurse -Force $BUILD_DIR -ErrorAction SilentlyContinue
    Remove-Item -Force "$OUT_NAME.spec" -ErrorAction SilentlyContinue
    Remove-Item -Force "version_info.txt" -ErrorAction SilentlyContinue

    # -- STEP 11: Installer (Inno Setup)
    Log "[11/11] Building the installer..."

    $iscc = Find-InnoSetup
    if (-not $iscc -and -not $NonInteractive -and (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        Log "  Inno Setup 6.6+ not found - installing it with winget (one time only)..." "Yellow"
        & winget.exe install --id JRSoftware.InnoSetup --exact --silent --accept-source-agreements --accept-package-agreements | Out-Host
        $iscc = Find-InnoSetup
    }

    $setupExe = Join-Path $DIST_DIR "$OUT_NAME-Setup.exe"
    if ($iscc) {
        Log "  Inno Setup $($iscc.Version): $($iscc.Path)" "Green"
        $appDirFull = (Resolve-Path $appDir).Path
        if ($NonInteractive) { Write-Host "::notice title=Inno Setup::Using Inno Setup $($iscc.Version)" }
        $isccOut = Invoke-Tool $iscc.Path @("/Qp", "/DAppVersion=$APP_VER", "/DAppBuildDir=$appDirFull", "installer\TunnelPulse.iss")
        if ($LASTEXITCODE -ne 0) { Fail "Inno Setup exited with code $LASTEXITCODE. $(Tail $isccOut)" }
        if (-not (Test-Path $setupExe)) { Fail "Installer not found: $setupExe" }
        Log "  Installer: $setupExe" "Green"
    } elseif ($NonInteractive) {
        $seen = if ($script:InnoSeen) { $script:InnoSeen -join "; " } else { "no ISCC.exe found" }
        Fail "Inno Setup 6.6 or newer is required to build the installer. Found: $seen"
    } else {
        Log "  Skipped: Inno Setup 6.6+ isn't installed. Install it from https://jrsoftware.org/isdl.php" "Yellow"
        Log "  (or run: winget install JRSoftware.InnoSetup), then build again." "Yellow"
        $setupExe = $null
    }

    Log "" "White"
    Log "=== BUILD COMPLETE ($APP_NAME $APP_VER) ===" "Magenta"
    if ($setupExe) { Log "Installer (share this): $((Get-Item $setupExe).FullName)" "Green" }
    Log "Portable zip          : $((Get-Item $zipName).FullName)" "Green"
    Log "App folder            : $((Get-Item $appDir).FullName)" "Green"
    Log "" "White"
    Log "Notes:" "Cyan"
    Log " - The installer sets up WireGuard if it's missing. The portable zip" "Cyan"
    Log "   doesn't, so that PC needs WireGuard for Windows installed already" "Cyan"
    Log "   (or wireguard.exe/wg.exe/wintun.dll in a bin\ folder at build time)." "Cyan"
    Log " - Both are unsigned, so Windows SmartScreen shows an 'unknown publisher'" "Cyan"
    Log "   warning the first time (More info -> Run anyway). Only a code-signing" "Cyan"
    Log "   certificate removes it." "Cyan"
    Log " - Pressing Connect asks for admin rights (it installs the WireGuard" "Cyan"
    Log "   tunnel service). Launching the app itself does not." "Cyan"

    if (-not $NonInteractive) {
        $show = if ($setupExe) { $setupExe } else { $zipName }
        Start-Process explorer.exe -ArgumentList "/select,`"$((Get-Item $show).FullName)`""
    }

}
catch {
    $script:BuildFailed = $true
    Log "" "White"
    Log "=== BUILD FAILED ===" "Red"
    Log "Error   : $($_.Exception.Message)" "Red"
    if ($NonInteractive) { Write-Host "::error title=Build failed::$($_.Exception.Message)" }
    Log "Location: $($_.InvocationInfo.PositionMessage)" "Yellow"
    Log "" "White"
    Log "Everything above (plus pip/PyInstaller's own output) is saved in the" "Yellow"
    Log "log file below - that's the file to send if you need help debugging." "Yellow"
}
finally {
    Log "" "White"
    Log "Full log file: $LogFile" "Cyan"
    try { Stop-Transcript -ErrorAction SilentlyContinue | Out-Null } catch {}
    if (-not $NonInteractive) {
        Write-Host ""
        Read-Host "Press Enter to close this window"
    }
}

if ($script:BuildFailed) { exit 1 } else { exit 0 }
