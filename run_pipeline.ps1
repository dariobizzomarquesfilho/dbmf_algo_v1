#Requires -Version 5.1

param(
    [switch]$NoTests,
    [switch]$NoBacktest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

trap {
    Write-Host "UNCAUGHT ERROR: $_" -ForegroundColor Red
    try { Stop-Transcript | Out-Null } catch {}
    exit 1
}

# --- Venv activation (fail-fast if missing) ---------------------------------
$VenvActivate = Join-Path $RepoRoot ".venv\Scripts\Activate.ps1"
if (-not (Test-Path $VenvActivate)) {
    Write-Host "ERROR: virtualenv not found at $VenvActivate" -ForegroundColor Red
    Write-Host "  Create it first, e.g.:" -ForegroundColor Yellow
    Write-Host "    python -m venv .venv" -ForegroundColor Yellow
    Write-Host "    .\.venv\Scripts\Activate.ps1" -ForegroundColor Yellow
    Write-Host "    pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}
. $VenvActivate

# --- Logging ----------------------------------------------------------------
try {
    Start-Transcript -Path (Join-Path $RepoRoot "run_pipeline.log") -Append | Out-Null
} catch {
    Write-Host "WARN: could not start transcript: $_" -ForegroundColor Yellow
}

# --- Step runner ------------------------------------------------------------
$script:Idx = 0
$script:TotalSteps = 0

function Invoke-Step {
    param([string]$Name, [scriptblock]$Action, [switch]$Soft)
    $script:Idx++
    Write-Progress -Activity "DBMF Quant Pipeline" -Status $Name `
                   -PercentComplete (100 * $script:Idx / $script:TotalSteps)
    Write-Host "`n===== [$script:Idx/$script:TotalSteps] $Name =====" -ForegroundColor Cyan
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $LASTEXITCODE = 0
    & $Action
    $code = $LASTEXITCODE
    $sw.Stop()
    if ($code -ne 0) {
        if ($Soft) {
            Write-Host "WARN: $Name exited $code (non-fatal, continuing)" -ForegroundColor Yellow
        } else {
            Write-Host "FAILED: $Name (exit code $code) after $($sw.Elapsed.TotalSeconds)s" -ForegroundColor Red
            Write-Progress -Activity "DBMF Quant Pipeline" -Completed
            exit $code
        }
    } else {
        Write-Host "OK: $Name ($($sw.Elapsed.TotalSeconds)s)" -ForegroundColor Green
    }
}

$steps = [System.Collections.Generic.List[hashtable]]::new()
function Add-Step($Name, $Action, $Soft=$false, $SkipIf=$null, $SkipIfNeg=$null) {
    if ($SkipIf -and -not (Test-Path $SkipIf)) {
        Write-Host "INFO: skip '$Name'" -ForegroundColor DarkGray
        return
    }
    if ($null -ne $SkipIfNeg -and -not $SkipIfNeg) {
        Write-Host "INFO: skip '$Name' (disabled)" -ForegroundColor DarkGray
        return
    }
    $steps.Add(@{ Name=$Name; Action=$Action; Soft=$Soft })
}

# A — Damodaran ERP PIT pipeline (REQUIRED by embed after Part 1a; sole ERP source)
Add-Step "Download Damodaran ERP archive (HTTP)"   { python implied_erp/scripts/download_damodaran_erp.py }
Add-Step "Extract all Damodaran ERP periods"       { python implied_erp/scripts/extract_all_damodaran_erp.py }
Add-Step "Build Lean PIT ERP history"              { python implied_erp/scripts/build_lean_erp_history.py }

# C — Lean data regeneration
Add-Step "Download edgartools PIT fundamentals"    { python lean_project/scripts/download_edgartools_data.py }
Add-Step "Download equity bars"                    { python lean_project/scripts/download_equity_data.py }
Add-Step "Convert to QC zip format"                { python lean_project/scripts/convert_to_qc_format.py }
Add-Step "Embed data into Lean modules"            { python lean_project/scripts/embed_data.py }   # hard-requires PIT history

# D — Tests (SOFT: report, continue)
Add-Step "Run lean_project tests"  { python -m pytest lean_project/tests } -Soft $true -SkipIfNeg (-not $NoTests)
Add-Step "Run implied_erp tests"   { python -m pytest implied_erp/tests }  -Soft $true -SkipIfNeg (-not $NoTests)

# E — Backtest (hard) — cwd must be lean_project
Add-Step "Run Lean backtest" {
    Push-Location lean_project
    try {
        lean backtest .
        $backtestCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    # Propagate lean's exit code (Pop-Location above would otherwise reset it)
    if ($backtestCode -ne 0) { exit $backtestCode }
} -SkipIfNeg (-not $NoBacktest)

# --- Finalize ---------------------------------------------------------------
$script:TotalSteps = $steps.Count
foreach ($s in $steps) {
    Invoke-Step -Name $s.Name -Action $s.Action -Soft:$s.Soft
}
Write-Progress -Activity "DBMF Quant Pipeline" -Completed
Write-Host "`nPIPELINE COMPLETE - all $($script:TotalSteps) steps succeeded." -ForegroundColor Green
try { Stop-Transcript | Out-Null } catch {}
exit 0
