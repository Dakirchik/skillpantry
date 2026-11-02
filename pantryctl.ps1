<#
.SYNOPSIS
  pantryctl - install and manage skillpantry (Windows; also works in PowerShell 7 on Linux/macOS).

.DESCRIPTION
  skillpantry's logic is Python; this script handles the system side: finds or installs
  Python and Git, creates the skillpantry / pantry / pantryctl commands on PATH, writes
  config.toml, updates and removes the installation. User projects are never touched.
  Language: -Lang en|ru, SKILLPANTRY_LANG, `language` in config.toml, or the Windows UI language.

.EXAMPLE
  .\pantryctl.ps1 install
  .\pantryctl.ps1 install -Library D:\Skills -Yes
  .\pantryctl.ps1 install -Lang en
  pantryctl status
  pantryctl update
  pantryctl uninstall -Purge
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Command = "help",
    [string]$Library,
    [string]$Catalog,
    [string]$Python,
    [switch]$Yes,
    [switch]$Purge,
    [switch]$NoPath,
    [ValidateSet("", "en", "ru", "auto")][string]$Lang = ""
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# ---- environment ---------------------------------------------------------------------

$OnWindows = ($PSVersionTable.PSEdition -eq "Desktop") -or ((Get-Variable IsWindows -ValueOnly -ErrorAction SilentlyContinue) -eq $true)
$Source = $PSScriptRoot
$HomeDir = if ($env:USERPROFILE -and $OnWindows) { $env:USERPROFILE } else { $HOME }

if ($env:SKILLPANTRY_APPDIR) { $AppDir = $env:SKILLPANTRY_APPDIR }
elseif ($OnWindows) { $AppDir = Join-Path $env:LOCALAPPDATA "skillpantry" }
else {
    $xdg = if ($env:XDG_DATA_HOME) { $env:XDG_DATA_HOME } else { Join-Path $HomeDir ".local/share" }
    $AppDir = Join-Path $xdg "skillpantry"
}
$BinDir = if ($env:SKILLPANTRY_BINDIR) { $env:SKILLPANTRY_BINDIR } else { Join-Path $AppDir "bin" }
$DataDir = if ($env:SKILLPANTRY_HOME) { $env:SKILLPANTRY_HOME } else { Join-Path $HomeDir ".skillpantry" }
$StateFile = Join-Path $AppDir "install.json"
$ConfigFile = Join-Path $DataDir "config.toml"
$MinPy = [Version]"3.11"

# ---- language: same priority as in Python (skillpantry/i18n.py) -------------------------
function Get-Lang {
    if ($Lang -in @("en", "ru")) { return $Lang }
    $v = $env:SKILLPANTRY_LANG
    if ($v -and $v.Length -ge 2 -and $v.Substring(0, 2).ToLower() -in @("en", "ru")) { return $v.Substring(0, 2).ToLower() }
    if ($Lang -ne "auto" -and (Test-Path $ConfigFile)) {
        $m = Select-String -Path $ConfigFile -Pattern '^\s*language\s*=\s*"(en|ru)' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($m) { return $m.Matches[0].Groups[1].Value }
    }
    if ((Get-UICulture).TwoLetterISOLanguageName -eq "ru") { return "ru" }
    return "en"
}
$UiLang = Get-Lang
function T([string]$en, [string]$ru) { if ($UiLang -eq "ru") { return $ru } return $en }

function Step($t) { Write-Host ""; Write-Host "> $t" -ForegroundColor Cyan }
function Ok($t) { Write-Host "  [ok] $t" -ForegroundColor Green }
function Info($t) { Write-Host "  $t" }
function Warn($t) { Write-Host "  [!] $t" -ForegroundColor Yellow }
function Fail($t) { Write-Host "  [x] $t" -ForegroundColor Red; exit 1 }

function Ask([string]$question, [bool]$default = $true) {
    if ($Yes) { return $true }
    $hint = if ($default) { "Y/n" } else { "y/N" }  # Enter = the capital option; y/yes/д/да accepted
    $a = Read-Host "  $question ($hint)"
    if ([string]::IsNullOrWhiteSpace($a)) { return $default }
    return ($a.Trim().ToLower() -in @("y", "yes", "д", "да", "l"))  # "l" is "y" on a Russian layout
}

function Get-Version {
    $init = Join-Path $Source "skillpantry/__init__.py"
    if (-not (Test-Path $init)) { return "?" }
    $m = Select-String -Path $init -Pattern '__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value } else { return "?" }
}

function Read-State {
    if (Test-Path $StateFile) { return Get-Content $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json }
    return $null
}

function Write-Utf8([string]$path, [string]$text) {
    $dir = Split-Path $path -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

# ---- Python ---------------------------------------------------------------------------

function Test-PythonCandidate([string]$exe, [string[]]$pre) {
    $ErrorActionPreference = "Continue"  # see Cmd-Update: stderr of `py -3.14` must not throw in 5.1
    try {
        # Single quotes only: Windows PowerShell 5.1 does not escape " in arguments of native
        # programs, so python would get broken code (SyntaxError) and Python would be "not found".
        $code = 'import sys;print(sys.executable);print(''%d.%d.%d'' % sys.version_info[:3])'
        $out = & $exe @pre -c $code 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        $lines = @($out)
        if ($lines.Count -lt 2) { return $null }
        if ([Version]$lines[1] -lt $MinPy) { return $null }
        return [pscustomobject]@{ Path = $lines[0].Trim(); Version = $lines[1].Trim() }
    } catch { return $null }
}

function Find-Python {
    if ($Python) {
        $p = Test-PythonCandidate $Python @()
        if (-not $p) { Fail (T "$Python is not Python $MinPy+" "$Python - не Python $MinPy+") }
        return $p
    }
    $candidates = @()
    if ($OnWindows -and (Get-Command py -ErrorAction SilentlyContinue)) {
        foreach ($v in "3.14", "3.13", "3.12", "3.11") { $candidates += , @("py", @("-$v")) }
    }
    foreach ($n in "python3.13", "python3.12", "python3.11", "python3", "python") {
        $cmd = Get-Command $n -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($cmd -and $cmd.Source -notmatch "WindowsApps") { $candidates += , @($cmd.Source, @()) }
    }
    if ($OnWindows) {
        # registry (PEP 514): finds python.org and winget installs even when they are not on PATH
        foreach ($hive in "HKCU:", "HKLM:", "HKLM:\SOFTWARE\WOW6432Node") {
            $root = if ($hive -like "*WOW6432Node") { "$hive\Python\PythonCore" } else { "$hive\Software\Python\PythonCore" }
            if (-not (Test-Path $root)) { continue }
            Get-ChildItem $root -ErrorAction SilentlyContinue | Sort-Object PSChildName -Descending | ForEach-Object {
                $ip = Join-Path $_.PSPath "InstallPath"
                $exe = (Get-ItemProperty $ip -ErrorAction SilentlyContinue).ExecutablePath
                if (-not $exe) {
                    $dir = (Get-ItemProperty $ip -ErrorAction SilentlyContinue).'(default)'
                    if ($dir) { $exe = Join-Path $dir "python.exe" }
                }
                if ($exe -and (Test-Path $exe)) { $candidates += , @($exe, @()) }
            }
        }
        foreach ($r in @((Join-Path $env:LOCALAPPDATA "Programs\Python"), $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
            if (-not $r -or -not (Test-Path $r)) { continue }
            Get-ChildItem $r -Directory -Filter "Python3*" -ErrorAction SilentlyContinue | Sort-Object Name -Descending |
                ForEach-Object {
                    $exe = Join-Path $_.FullName "python.exe"
                    if (Test-Path $exe) { $candidates += , @($exe, @()) }
                }
        }
    }
    foreach ($c in $candidates) {
        $r = Test-PythonCandidate $c[0] $c[1]
        if ($r) { return $r }
    }
    return $null
}

function Update-SessionPath {
    if (-not $OnWindows) { return }
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ";"
}

function Install-WithWinget([string]$id, [string]$what) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { return $false }
    if (-not (Ask (T "Install $what with winget (for the current user)?" "Установить $what через winget (для текущего пользователя)?"))) { return $false }
    winget install -e --id $id --scope user --accept-package-agreements --accept-source-agreements | Out-Host
    Update-SessionPath
    return $true
}

function Ensure-Python {
    Step "Python $MinPy+"
    $py = Find-Python
    if (-not $py -and $OnWindows) {
        Warn (T "Python $MinPy+ not found" "Python $MinPy+ не найден")
        if (Install-WithWinget "Python.Python.3.12" "Python 3.12") { $py = Find-Python }
    }
    if (-not $py) {
        if ($OnWindows) {
            Info (T "If Python is already installed, pass it explicitly: .\pantryctl.ps1 install -Python 'C:\path\to\python.exe'" "Если Python уже стоит, укажите его явно: .\pantryctl.ps1 install -Python 'C:\путь\к\python.exe'")
            Info (T "(find the path: py -0p   or   where.exe python)" "(узнать путь: py -0p   или   where.exe python)")
            Fail (T "Install Python 3.12 from https://www.python.org/downloads/ (tick 'Add to PATH') and run again" "Установите Python 3.12 с https://www.python.org/downloads/ (галочка 'Add to PATH') и повторите")
        }
        Fail (T "Install Python 3.11+ (macOS: brew install python@3.12; Linux: your package manager) or use pantryctl.sh" "Установите Python 3.11+ (macOS: brew install python@3.12; Linux: пакетный менеджер) или используйте pantryctl.sh")
    }
    Ok "Python $($py.Version): $($py.Path)"
    return $py
}

function Ensure-Git {
    Step "Git"
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git -and $OnWindows) {
        Warn (T "Git not found - it is needed to fetch skills at pinned commits" "Git не найден - он нужен, чтобы скачивать скиллы с закреплённых коммитов")
        if (Install-WithWinget "Git.Git" "Git") { $git = Get-Command git -ErrorAction SilentlyContinue }
    }
    if (-not $git) { Warn (T "Git not found: install https://git-scm.com - without it skillpantry cannot fetch sources" "Git не найден: установите https://git-scm.com - без него skillpantry не сможет скачивать источники"); return }
    Ok ((& git --version) -join "")
}

# ---- commands on PATH ----------------------------------------------------------------------

function ConvertTo-CmdPath([string]$path) {
    # cmd.exe reads .cmd files in the OEM code page, so a non-ASCII user name breaks a
    # literal path. Paths under the profile are written via variables that cmd expands
    # at run time (same trick as agent-recall); '%' itself must be doubled in a batch file.
    $p = $path -replace '%', '%%'
    foreach ($v in "LOCALAPPDATA", "USERPROFILE") {
        $val = [Environment]::GetEnvironmentVariable($v)
        if ($val -and $p.StartsWith($val.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) {
            return "%$v%" + $p.Substring($val.TrimEnd('\').Length)
        }
    }
    return $p
}

function Write-Cmd([string]$path, [string]$text) {
    $dir = Split-Path $path -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $enc = [System.Text.Encoding]::ASCII
    if ($text -match '[^\x00-\x7F]') {
        $enc = $null
        try {
            $oem = [System.Text.Encoding]::GetEncoding([Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage)
            if ($oem.GetString($oem.GetBytes($text)) -eq $text) { $enc = $oem }
        } catch { }
        if (-not $enc) {
            Fail (T "The path has characters cmd.exe cannot read: $path. Move skillpantry to a folder with Latin letters and run install again." "Путь содержит символы, которые cmd.exe не прочитает: $path. Переместите skillpantry в папку с латинскими буквами и повторите install.")
        }
    }
    [System.IO.File]::WriteAllText($path, $text, $enc)
}

function Write-Shims($py) {
    Step (T "Commands skillpantry, pantry, pantryctl" "Команды skillpantry, pantry, pantryctl")
    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
    if ($OnWindows) {
        $src = ConvertTo-CmdPath $Source
        $exe = ConvertTo-CmdPath $py.Path
        $run = "@echo off`r`nset ""PYTHONPATH=$src;%PYTHONPATH%""`r`n""$exe"" -X utf8 -m skillpantry %*`r`n"
        Write-Cmd (Join-Path $BinDir "skillpantry.cmd") $run
        Write-Cmd (Join-Path $BinDir "pantry.cmd") $run
        $ctl = "@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -File ""$src\pantryctl.ps1"" %*`r`n"
        Write-Cmd (Join-Path $BinDir "pantryctl.cmd") $ctl
    } else {
        $run = "#!/bin/sh`nPYTHONPATH='$Source'`${PYTHONPATH:+:`$PYTHONPATH}`nexport PYTHONPATH`nexec '$($py.Path)' -X utf8 -m skillpantry `"`$@`"`n"
        foreach ($n in "skillpantry", "pantry") {
            $f = Join-Path $BinDir $n
            Write-Utf8 $f $run
            & chmod +x $f
        }
        $f = Join-Path $BinDir "pantryctl"
        $pwshExe = (Get-Process -Id $PID).Path
        Write-Utf8 $f "#!/bin/sh`nexec '$pwshExe' -NoProfile -File '$Source/pantryctl.ps1' `"`$@`"`n"
        & chmod +x $f
    }
    Ok (T "in $BinDir" "в $BinDir")
}

function Test-InPath([string]$dir) {
    $sep = [IO.Path]::PathSeparator
    $norm = $dir.TrimEnd('\', '/')
    foreach ($p in ($env:PATH -split [regex]::Escape($sep))) {
        if ($p -and $p.TrimEnd('\', '/') -eq $norm) { return $true }
    }
    return $false
}

function Add-ToUserPath {
    if ($NoPath) { Info (T "PATH left as is (-NoPath). The commands are in $BinDir" "PATH не меняю (-NoPath). Команды лежат в $BinDir"); return }
    if (-not $OnWindows) {
        if (-not (Test-InPath $BinDir)) { Warn (T "$BinDir is not on PATH. On Linux/macOS install with pantryctl.sh - it adds PATH itself." "$BinDir нет в PATH. На Linux/macOS удобнее ставить через pantryctl.sh - он добавит PATH сам.") }
        return
    }
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @($user -split ";" | Where-Object { $_ })
    if ($parts -notcontains $BinDir) {
        [Environment]::SetEnvironmentVariable("Path", (($parts + $BinDir) -join ";"), "User")
        Ok (T "added to the user PATH (new terminal windows will see the commands)" "добавлено в PATH пользователя (новые окна терминала увидят команды)")
    } else { Ok (T "already on PATH" "уже в PATH") }
    if (-not (Test-InPath $BinDir)) { $env:Path = "$env:Path;$BinDir" }
}

function Remove-FromUserPath {
    if (-not $OnWindows) { return }
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $user) { return }
    $parts = @($user -split ";" | Where-Object { $_ -and $_ -ne $BinDir })
    [Environment]::SetEnvironmentVariable("Path", ($parts -join ";"), "User")
}

# ---- config.toml -------------------------------------------------------------------------

function ConvertTo-TomlString([string]$s) {
    return '"' + ($s -replace '\\', '\\' -replace '"', '\"') + '"'
}

function Set-ConfigValue([string]$key, [string]$value) {
    $line = "$key = $(ConvertTo-TomlString $value)"
    $text = if (Test-Path $ConfigFile) { [System.IO.File]::ReadAllText($ConfigFile) } else { "" }
    $rx = "(?m)^\s*#?\s*$([regex]::Escape($key))\s*=.*$"
    if ($text -match $rx) { $text = [regex]::Replace($text, $rx, $line, 1) }
    else { $text = $text.TrimEnd() + "`n" + $line + "`n" }
    Write-Utf8 $ConfigFile ($text.TrimStart())
}

function Ensure-Config {
    Step (T "Settings" "Настройки")
    if (-not (Test-Path $ConfigFile)) {
        $defaultLib = Join-Path $HomeDir "skillpantry-library"
        $tpl = @"
# skillpantry settings. SKILLPANTRY_* environment variables win over this file.

# Interface language: "en" or "ru" (empty - follow the system)
# language = "en"

# Folder for the folder target (-t folder) when -d is not given
# library = $(ConvertTo-TomlString $defaultLib)

# Your own folder with catalog manifests (default - skillpantry\catalog in the sources)
# catalog = "D:/my-catalog"
"@
        Write-Utf8 $ConfigFile ($tpl.Replace("`r`n", "`n") + "`n")
        Ok (T "created $ConfigFile" "создан $ConfigFile")
    } else { Ok (T "exists: $ConfigFile" "есть $ConfigFile") }
    if ($Lang) {
        Set-ConfigValue "language" $(if ($Lang -eq "auto") { "" } else { $Lang })
        Ok "language = $Lang"
    }
    if ($Library) { Set-ConfigValue "library" ([IO.Path]::GetFullPath($Library)); Ok "library = $Library" }
    if ($Catalog) { Set-ConfigValue "catalog" ([IO.Path]::GetFullPath($Catalog)); Ok "catalog = $Catalog" }
}

# ---- running the Python part -----------------------------------------------------------------

function Invoke-Shelf([string[]]$argList) {
    $state = Read-State
    if (-not $state) { Fail (T "skillpantry is not installed. Run: .\pantryctl.ps1 install" "skillpantry не установлен. Запустите: .\pantryctl.ps1 install") }
    $old = $env:PYTHONPATH
    $sep = [IO.Path]::PathSeparator
    $env:PYTHONPATH = if ($old) { "$($state.source)$sep$old" } else { $state.source }
    try { & $state.python -X utf8 -m skillpantry @argList | Out-Host; return $LASTEXITCODE }
    finally { $env:PYTHONPATH = $old }
}

# ---- before 0.5.0 the project was called skillshelf: take over that installation --------------

function Move-Legacy($py) {
    $legacyHome = Join-Path $HomeDir ".skillshelf"
    if (-not $env:SKILLPANTRY_HOME -and (Test-Path $legacyHome) -and -not (Test-Path $DataDir)) {
        # Python moves the data and fixes the paths in the install journal
        $old = $env:PYTHONPATH
        $env:PYTHONPATH = $Source
        try { & $py.Path -c 'from skillpantry.config import home_dir; home_dir()' } finally { $env:PYTHONPATH = $old }
        if (Test-Path $DataDir) { Ok (T "data moved: $legacyHome -> $DataDir" "данные перенесены: $legacyHome -> $DataDir") }
    }
    $legacyApp = if ($OnWindows) { Join-Path $env:LOCALAPPDATA "skillshelf" } else {
        $xdg = if ($env:XDG_DATA_HOME) { $env:XDG_DATA_HOME } else { Join-Path $HomeDir ".local/share" }
        Join-Path $xdg "skillshelf" }
    if (-not (Test-Path $legacyApp) -or $legacyApp -eq $AppDir) { return }
    $legacyBin = Join-Path $legacyApp "bin"
    foreach ($n in "skillshelf.cmd", "shelf.cmd", "shelfctl.cmd", "skillshelf", "shelf", "shelfctl") {
        $f = Join-Path $legacyBin $n
        if (Test-Path $f) { Remove-Item $f -Force }
    }
    if ($OnWindows) {
        $user = [Environment]::GetEnvironmentVariable("Path", "User")
        if ($user) {
            $parts = @($user -split ";" | Where-Object { $_ -and $_ -ne $legacyBin })
            [Environment]::SetEnvironmentVariable("Path", ($parts -join ";"), "User")
        }
    }
    $legacyState = Join-Path $legacyApp "install.json"
    if (Test-Path $legacyState) { Remove-Item $legacyState -Force }
    foreach ($d in @($legacyBin, $legacyApp)) {
        if ((Test-Path $d) -and -not (Get-ChildItem $d -Force | Select-Object -First 1)) { Remove-Item $d -Force }
    }
    Ok (T "the old skillshelf commands were replaced" "старые команды skillshelf заменены")
}

# ---- commands -----------------------------------------------------------------------------

function Cmd-Install {
    Write-Host (T "skillpantry $(Get-Version) - install" "skillpantry $(Get-Version) - установка") -ForegroundColor White
    Info (T "sources: $Source" "исходники: $Source")
    if (-not (Test-Path (Join-Path $Source "skillpantry/__init__.py"))) { Fail (T "No skillpantry folder next to the script - run pantryctl.ps1 from the repository root" "Рядом со скриптом нет папки skillpantry - запустите pantryctl.ps1 из корня репозитория") }
    $py = Ensure-Python
    Ensure-Git
    Move-Legacy $py
    Write-Shims $py
    Add-ToUserPath
    Ensure-Config
    New-Item -ItemType Directory -Path $AppDir -Force | Out-Null
    $state = [ordered]@{ version = (Get-Version); source = $Source; python = $py.Path; bin = $BinDir
        installed_at = (Get-Date).ToString("s"); platform = $(if ($OnWindows) { "windows" } else { "unix-pwsh" }) }
    Write-Utf8 $StateFile ($state | ConvertTo-Json)
    Step (T "Check" "Проверка")
    $code = Invoke-Shelf @("validate")
    if ($code -ne 0) { Warn (T "the catalog has errors (see above) - the install works, but those entries should be fixed" "в каталоге есть ошибки (см. выше) - установка работает, но эти записи лучше поправить") }
    Write-Host ""
    Write-Host (T "Done." "Готово.") -ForegroundColor Green
    Info (T "Open a new terminal window and run:  skillpantry" "Откройте новое окно терминала и запустите:  skillpantry")
    Info (T "List for Claude Code:                skillpantry list -t claude-code" "Список для Claude Code:                       skillpantry list -t claude-code")
    Info (T "Manage the installation:             pantryctl status | update | uninstall" "Управление установкой:                        pantryctl status | update | uninstall")
    Info (T "Language:                            skillpantry lang en | ru" "Язык:                                         skillpantry lang en | ru")
    Info (T "The sources stay here and are used directly: $Source" "Исходники остаются здесь и используются напрямую: $Source")
    Info (T "If you move the folder - run install again." "Если переместите папку - запустите install ещё раз.")
}

function Cmd-Update {
    $state = Read-State
    if (-not $state) { Fail (T "skillpantry is not installed. Run: .\pantryctl.ps1 install" "skillpantry не установлен. Запустите: .\pantryctl.ps1 install") }
    Step (T "Updating the sources" "Обновление исходников")
    if ((Test-Path (Join-Path $Source ".git")) -and (Get-Command git -ErrorAction SilentlyContinue)) {
        # Windows PowerShell 5.1 turns a native command's stderr into a terminating error
        # under "Stop" once it is redirected; git writes progress and "no upstream" there.
        $ErrorActionPreference = "Continue"
        & git -C $Source rev-parse --abbrev-ref --symbolic-full-name "@{u}" *> $null
        if ($LASTEXITCODE -eq 0) {
            & git -C $Source pull --ff-only 2>&1 | Out-Host
            if ($LASTEXITCODE -ne 0) { Warn (T "git pull failed (local changes?) - continuing with the current sources" "git pull не прошёл (локальные изменения?) - продолжаю с текущими исходниками") }
        } else { Info (T "the branch has no upstream (git push -u origin main) - skipping git pull" "у ветки нет удалённого репозитория (git push -u origin main) - пропускаю git pull") }
        $ErrorActionPreference = "Stop"
    } else { Info (T "not a git repository - update the files yourself (a new archive) and run update again" "не git-репозиторий - обновите файлы сами (новый архив) и повторите update") }
    $py = Test-PythonCandidate $state.python @()
    if (-not $py) { Warn (T "the saved Python no longer works, looking again" "сохранённый Python больше не работает, ищу заново"); $py = Ensure-Python }
    Write-Shims $py
    $state.version = Get-Version
    $state.source = $Source
    $state.python = $py.Path
    Write-Utf8 $StateFile ($state | ConvertTo-Json)
    Step (T "Check" "Проверка")
    [void](Invoke-Shelf @("validate"))
    Info (T "Entries with a newer commit in the catalog: skillpantry status --all" "Записи, у которых в каталоге новее коммит, покажет: skillpantry status --all")
}

function Cmd-Uninstall {
    Step (T "Removing skillpantry" "Удаление skillpantry")
    $installs = Join-Path $DataDir "installs.json"
    if ($Purge -and (Test-Path $installs)) {
        $n = 0
        try { $n = @((Get-Content $installs -Raw -Encoding UTF8 | ConvertFrom-Json).installs.PSObject.Properties).Count } catch { }
        if ($n -gt 0) {
            Warn (T "skillpantry installed entries in $n place(s). Project files stay, but skillpantry uninstall will no longer be able to remove them." "skillpantry поставил записи в $n мест(а). Файлы в проектах останутся, но удалить их командой uninstall потом будет нельзя.")
            Info (T "See: skillpantry status --all   ·   remove: skillpantry uninstall <id> -t <target> ..." "Посмотреть: skillpantry status --all   ·   удалить: skillpantry uninstall <id> -t <цель> ...")
            if (-not (Ask (T "Delete the install journal and cache anyway?" "Всё равно удалить журнал установок и кэш?") $false)) { $script:Purge = $false }
        }
    }
    foreach ($n in "skillpantry.cmd", "pantry.cmd", "pantryctl.cmd", "skillpantry", "pantry", "pantryctl") {
        $f = Join-Path $BinDir $n
        if (Test-Path $f) { Remove-Item $f -Force }
    }
    Remove-FromUserPath
    if (Test-Path $StateFile) { Remove-Item $StateFile -Force }
    foreach ($d in @($BinDir, $AppDir)) {
        if ((Test-Path $d) -and -not (Get-ChildItem $d -Force | Select-Object -First 1)) { Remove-Item $d -Force }
    }
    Ok (T "commands and PATH removed" "команды и PATH убраны")
    if ($Purge) {
        if (Test-Path $DataDir) {
            $ownDir = Join-Path $DataDir "catalog"
            $own = @()
            if (Test-Path $ownDir) { $own = @(Get-ChildItem $ownDir -Filter *.toml -File | Where-Object { -not $_.Name.StartsWith("_") }) }
            $keepOwn = $false
            if ($own.Count -gt 0) {
                Warn (T "$ownDir holds your own entries ($($own.Count)) - there are no other copies." "В $ownDir ваши собственные записи ($($own.Count)) - копий больше нигде нет.")
                # with -Yes your own entries are never deleted: only on an explicit answer
                if ($Yes) { $keepOwn = $true } else { $keepOwn = -not (Ask (T "Delete your own entries too?" "Удалить и свои записи?") $false) }
            }
            if ($keepOwn) {
                Get-ChildItem $DataDir -Force | Where-Object { $_.Name -ne "catalog" } | Remove-Item -Recurse -Force
                Ok (T "data removed: $DataDir (your own entries kept in $ownDir)" "удалены данные: $DataDir (свои записи оставлены в $ownDir)")
            } else {
                Remove-Item $DataDir -Recurse -Force; Ok (T "data removed: $DataDir" "удалены данные: $DataDir")
            }
        }
    } else { Info (T "Data (install journal, cache, backups, config.toml, your own entries) kept: $DataDir  (-Purge removes it)" "Данные (журнал установок, кэш, резервные копии, config.toml, свои записи) оставлены: $DataDir  (-Purge удалит)") }
    Info (T "Sources left untouched: $Source" "Исходники не трогаю: $Source")
}

function Cmd-Status {
    $state = Read-State
    Write-Host "skillpantry $(Get-Version)" -ForegroundColor White
    if (-not $state) { Warn (T "not installed (.\pantryctl.ps1 install)" "не установлен (.\pantryctl.ps1 install)"); return }
    $when = $state.installed_at
    if ($when -is [datetime]) { $when = $when.ToString("yyyy-MM-dd HH:mm") }
    Info (T "installed:    $when  (version $($state.version))" "установлен:   $when  (версия $($state.version))")
    Info ((T "sources:      " "исходники:    ") + $state.source + $(if (-not (Test-Path $state.source)) { T '  [!] the folder is gone - run install from the new folder' '  [!] папки нет - запустите install из новой папки' }))
    $ok = Test-PythonCandidate $state.python @()
    Info ("python:       $($state.python)" + $(if ($ok) { "  ($($ok.Version))" } else { T '  [!] does not start - pantryctl update' '  [!] не запускается - pantryctl update' }))
    Info ((T "commands:     " "команды:      ") + $BinDir + $(if (Test-InPath $BinDir) { '' } else { T '  (not on PATH in this window - open a new one)' '  (в этом окне не в PATH - откройте новое)' }))
    Info ((T "data:         " "данные:       ") + $DataDir)
    Info ((T "config:       " "конфиг:       ") + $ConfigFile + $(if (Test-Path $ConfigFile) { '' } else { T '  (none)' '  (нет)' }))
    Info ((T "language:     " "язык:         ") + $UiLang)
    Write-Host ""
    [void](Invoke-Shelf @("status", "--all"))
}

function Cmd-Paths {
    Info ((T "sources     " "исходники   ") + $Source)
    Info ((T "program     " "программа   ") + $AppDir)
    Info ((T "commands    " "команды     ") + $BinDir)
    Info ((T "data        " "данные      ") + $DataDir)
    Info ((T "config      " "конфиг      ") + $ConfigFile)
    Info ((T "own entries " "свои записи ") + (Join-Path $DataDir 'catalog'))
    Info ((T "cache       " "кэш         ") + (Join-Path $DataDir 'cache'))
    Info ((T "journal     " "журнал      ") + (Join-Path $DataDir 'installs.json'))
    Info ((T "backups     " "копии       ") + (Join-Path $DataDir 'backups'))
}

function Cmd-Config {
    if (-not (Test-Path $ConfigFile)) { Ensure-Config }
    if ($OnWindows) { Start-Process notepad.exe $ConfigFile } else { Info $ConfigFile }
}

function Cmd-Help {
    if ($UiLang -eq "ru") {
        Write-Host @"
pantryctl - установка и управление skillpantry

  install [-Library <папка>] [-Catalog <папка>] [-Python <путь>] [-Lang en|ru|auto] [-Yes] [-NoPath]
                    Python 3.11+ и Git (через winget, если нет) -> команды skillpantry,
                    pantry, pantryctl в PATH -> config.toml -> проверка каталога
  update            git pull исходников, пересоздать команды, проверить каталог
  uninstall [-Purge]
                    убрать команды и PATH; с -Purge ещё журнал, кэш и config.toml
                    (файлы, поставленные в проекты, не трогаются; свои записи - только по ответу)
  status            что установлено, где, какие записи стоят в проектах
  paths             все пути
  config            открыть config.toml
  doctor            проверка окружения (skillpantry doctor)

Работа с каталогом - команда skillpantry (или pantry):  skillpantry --help
Язык: -Lang en|ru, SKILLPANTRY_LANG или skillpantry lang en|ru
"@
        return
    }
    Write-Host @"
pantryctl - install and manage skillpantry

  install [-Library <folder>] [-Catalog <folder>] [-Python <path>] [-Lang en|ru|auto] [-Yes] [-NoPath]
                    Python 3.11+ and Git (via winget if missing) -> skillpantry, pantry,
                    pantryctl commands on PATH -> config.toml -> catalog check
  update            git pull the sources, recreate the commands, check the catalog
  uninstall [-Purge]
                    remove the commands and PATH; with -Purge also the journal, cache and
                    config.toml (files installed into projects stay; your own entries only if you say so)
  status            what is installed and where, which entries are in projects
  paths             all paths
  config            open config.toml
  doctor            environment check (skillpantry doctor)

Working with the catalog - the skillpantry (or pantry) command:  skillpantry --help
Language: -Lang en|ru, SKILLPANTRY_LANG or skillpantry lang en|ru
"@
}

switch ($Command.ToLower()) {
    "install" { Cmd-Install }
    "update" { Cmd-Update }
    "uninstall" { Cmd-Uninstall }
    "status" { Cmd-Status }
    "paths" { Cmd-Paths }
    "config" { Cmd-Config }
    "doctor" { exit (Invoke-Shelf @("doctor")) }
    { $_ -in "help", "-h", "--help", "/?" } { Cmd-Help }
    default { Write-Host (T "Unknown command: $Command" "Неизвестная команда: $Command") -ForegroundColor Red; Cmd-Help; exit 2 }
}
