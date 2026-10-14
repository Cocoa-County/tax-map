$ErrorActionPreference = 'Stop'
$work = Join-Path ([IO.Path]::GetTempPath()) ("taxmap-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$exitCode = 1
try {
    $python = $null
    foreach ($candidate in @('python', 'python3', 'py')) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) {
            $result = & $candidate -c 'import sys, importlib.util; ok = sys.version_info >= (3, 10) and all(importlib.util.find_spec(m) for m in ("venv", "ensurepip")); print(sys.executable) if ok else None; sys.exit(0 if ok else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) {
                $python = "$result".Trim()
                break
            }
        }
    }
    if (-not $python) {
        Write-Host 'Python 3.10+ with venv/pip was not found.'
        Write-Host 'Download uv from astral.sh and managed Python 3.13 into your user account?'
        $answer = Read-Host 'No administrator access or PATH changes. Continue? [y/N]'
        if ($answer -notmatch '^[yY]$') { throw 'Python bootstrap cancelled. Install Python 3.10+ with venv/pip and retry.' }
        $uvDir = Join-Path $HOME '.local\share\taxmap\bootstrap\bin'
        $previousInstall = $env:UV_UNMANAGED_INSTALL
        $previousPython = $env:UV_PYTHON_INSTALL_DIR
        try {
            $env:UV_UNMANAGED_INSTALL = $uvDir
            $env:UV_PYTHON_INSTALL_DIR = Join-Path $HOME '.local\share\taxmap\bootstrap\python'
            $uvScript = Join-Path $work 'uv-install.ps1'
            Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/0.13.0/install.ps1' -OutFile $uvScript
            & (Get-Process -Id $PID).Path -NoProfile -File $uvScript
            if ($LASTEXITCODE -ne 0) { throw 'uv installation failed.' }
            $uv = Join-Path $uvDir 'uv.exe'
            if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) { throw 'uv installation failed.' }
            & $uv python install 3.13 --no-bin
            if ($LASTEXITCODE -ne 0) { throw 'Managed Python download failed.' }
            $result = & $uv python find --managed-python 3.13
            if ($LASTEXITCODE -ne 0) { throw 'Managed Python was not found.' }
            $python = "$result".Trim()
        } finally {
            $env:UV_UNMANAGED_INSTALL = $previousInstall
            $env:UV_PYTHON_INSTALL_DIR = $previousPython
        }
    }
    if ($env:TAXMAP_SOURCE_DIR) {
        $source = (Resolve-Path -LiteralPath $env:TAXMAP_SOURCE_DIR).Path
        $setup = Join-Path $source 'scripts\setup_project.py'
        if (-not (Test-Path -LiteralPath $setup -PathType Leaf)) { throw 'TAXMAP_SOURCE_DIR must point to a taxmap checkout.' }
        & $python $setup --source $source
    } else {
        $ref = if ($env:TAXMAP_REF) { $env:TAXMAP_REF } else { 'main' }
        if ($ref -notmatch '^[A-Za-z0-9._/-]+$' -or $ref.Contains('..')) { throw 'Invalid TAXMAP_REF.' }
        $commit = Invoke-RestMethod -Uri "https://api.github.com/repos/Cocoa-County/tax-map/commits/$ref"
        $revision = $commit.sha
        if ($revision -notmatch '^[0-9a-f]{40}$') { throw 'GitHub did not return a valid repository revision.' }
        $setup = Join-Path $work 'setup.py'
        Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/Cocoa-County/tax-map/$revision/scripts/setup_project.py" -OutFile $setup
        & $python $setup --revision $revision
    }
    $exitCode = $LASTEXITCODE
} catch {
    Write-Error -ErrorAction Continue "taxmap setup failed: $_"
} finally {
    foreach ($name in @('setup.py', 'uv-install.ps1')) {
        $file = Join-Path $work $name
        if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file }
    }
    Remove-Item -LiteralPath $work
}
exit $exitCode
