[CmdletBinding()]
param(
    [switch]$SkipTaobaoCheck,
    [switch]$ConfigureEnterpriseKeys,
    [switch]$AuditOnly,
    [switch]$AllowPaidApi
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$skillRoot = Split-Path -Parent $scriptRoot
if ($ConfigureEnterpriseKeys -and -not $AllowPaidApi) {
    throw "-ConfigureEnterpriseKeys requires explicit -AllowPaidApi. Browser mode needs no API keys."
}

if ($AuditOnly -and $SkipTaobaoCheck) {
    throw "-AuditOnly and -SkipTaobaoCheck cannot be combined."
}

function Get-CompatiblePython {
    $candidates = [System.Collections.Generic.List[string]]::new()
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) {
        $candidates.Add($command.Source)
    }
    $searchRoots = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python*\python.exe"),
        (Join-Path $env:ProgramFiles "Python*\python.exe")
    )
    foreach ($pattern in $searchRoots) {
        Get-ChildItem -Path $pattern -File -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            ForEach-Object { $candidates.Add($_.FullName) }
    }
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        try {
            $versionText = & $candidate -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null
            if ($LASTEXITCODE -eq 0 -and [version]$versionText -ge [version]"3.11") {
                return $candidate
            }
        } catch {
            continue
        }
    }
    return $null
}

$python = Get-CompatiblePython
if (-not $python) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Python 3.11+ is missing and winget is unavailable. Install Python 3.11 or newer, then rerun this script."
    }
    Write-Host "Installing Python 3.11 for the current user..."
    & $winget.Source install --exact --id Python.Python.3.11 --scope user --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "Python installation failed with exit code $LASTEXITCODE."
    }
    $env:Path = "$(Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311');$(Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\Scripts');$env:Path"
    $python = Get-CompatiblePython
    if (-not $python) {
        throw "Python was installed but is not available yet. Restart Codex and rerun this script."
    }
}

Write-Host "Using Python: $python"
if ($ConfigureEnterpriseKeys) {
    & $python (Join-Path $scriptRoot "configure_enterprise_keys.py")
    if ($LASTEXITCODE -ne 0) {
        throw "Enterprise credential setup failed. Both QCC and Fengniao keys are required."
    }
}
$arguments = @((Join-Path $scriptRoot "preflight.py"), "--install-missing")
if ($AllowPaidApi) {
    $arguments += "--allow-paid-api"
}
if ($AuditOnly) {
    $arguments += "--audit-only"
}
if (-not $SkipTaobaoCheck) {
    $arguments += "--check-taobao"
} else {
    $arguments += "--enterprise-only"
}
& $python @arguments
exit $LASTEXITCODE
