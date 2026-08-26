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
    Write-Host "    pip install -r config/requirements.txt" -ForegroundColor Yellow
    exit 1
}
. $VenvActivate

# --- Logging (per-run folder: logs/<yyyyMMdd_HHmmss>/) --------------------------
$script:runStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$script:runLogDir = Join-Path $RepoRoot ("logs\" + $script:runStamp)
New-Item -ItemType Directory -Force -Path $script:runLogDir | Out-Null
try {
    Start-Transcript -Path (Join-Path $script:runLogDir "run_pipeline.log") | Out-Null
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

    $log = Join-Path $script:runLogDir ("step{0:00}-{1}.log" -f $script:Idx, $Name.Replace(' ', '_'))

    # Capture merged stdout/stderr into a variable FIRST so $LASTEXITCODE keeps
    # the action's real exit code (piping straight to Tee-Object would reset it
    # to Tee's). Then tee the captured stream to the per-step log file.
    #
    # A native command's stderr is routed here via 2>&1 into the output stream.
    # Under the script's $ErrorActionPreference='Stop' those writes would
    # otherwise be treated as terminating errors and abort the whole pipeline
    # (e.g. a harmless "[skip] ... already exists" line). Scope the capture to
    # 'Continue' so a verbose/warning line on stderr cannot fail the run —
    # genuine failures are still caught via the action's exit code below.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $raw = & $Action 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    # Flatten stderr ErrorRecords to plain strings so the console and per-step
    # log aren't cluttered with PowerShell's "NativeCommandError" wrappers.
    $stepOut = $raw | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() } else { $_ }
    }
    $stepOut | Tee-Object -FilePath $log | Out-Null

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
Add-Step "Repair equity bars (Yahoo retry)"         { python lean_project/scripts/repair_equity_data.py }
Add-Step "Recover missing delisted bars"           { python lean_project/scripts/fetch_missing_delisted.py --apply }
Add-Step "Track exclusions (missing-data report)"  { python lean_project/scripts/track_exclusions.py } -Soft $true
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
