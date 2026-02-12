<#
    Convenience wrappers for transit_process_pipeline on Windows.
    Adjust the values in $TransitPipelineDefaults to suit your setup.
#>

$Script:TransitPipelineDefaults = [ordered]@{
    Dataset = "C:\\Users\\Dane\\Pictures\\DSOs\\04_exoplanets\\Qatar-1 b\\no_filter\\DATE_09-23-2025"
    FitsRoot = "debayered_green"
    Apertures = $null
    AijExec = "C:\\Program Files\\AstroImageJ\\AstroImageJ.exe"
}

$Script:RepoRoot = Split-Path -Parent $PSScriptRoot
$Script:GenerateSkewsPath = Join-Path $RepoRoot "scripts\generate_skews.py"
$Script:BatchReducePath = Join-Path $RepoRoot "scripts\batch_reduce.py"
$Script:JobsDir = Join-Path $RepoRoot "aij\jobs"

function Invoke-TransitGenerateSkews {
    [CmdletBinding()]
    param(
        [string]$Dataset = $TransitPipelineDefaults.Dataset,
        [string]$FitsRoot = $TransitPipelineDefaults.FitsRoot,
        [string]$Apertures = $TransitPipelineDefaults.Apertures,
        [switch]$RunAIJ,
        [string]$AijExec,
        [string]$ExtraArgs
    )

    if (-not (Test-Path $Dataset)) {
        throw "Dataset directory not found: $Dataset"
    }

    $args = @($GenerateSkewsPath, "--dataset", $Dataset, "--fits-root", $FitsRoot)
    if ($Apertures) {
        $args += @("--apertures", $Apertures)
    }

    if ($RunAIJ) {
        $exec = if ($AijExec) { $AijExec } elseif ($TransitPipelineDefaults.AijExec) { $TransitPipelineDefaults.AijExec } else { $null }
        if (-not $exec) {
            throw "--run-aij requested but no AIJ executable path was provided."
        }
        if (-not (Test-Path $exec)) {
            throw "AIJ executable not found: $exec"
        }
        $args += @("--run-aij", "--aij-exec", $exec)
    }

    if ($ExtraArgs) {
        $args += $ExtraArgs.Split()
    }

    Write-Host "[generate-skews] python" ($args -join ' ')
    & python @args
}

function Invoke-TransitBatchReduce {
    [CmdletBinding()]
    param(
        [string]$Dataset = $TransitPipelineDefaults.Dataset,
        [string]$ExtraArgs
    )

    if (-not (Test-Path $Dataset)) {
        throw "Dataset directory not found: $Dataset"
    }

    $args = @($BatchReducePath, "--dataset", $Dataset)
    if ($ExtraArgs) {
        $args += $ExtraArgs.Split()
    }

    Write-Host "[batch-reduce] python" ($args -join ' ')
    & python @args
}

function Invoke-TransitMacro {
    [CmdletBinding()]
    param(
        [string]$JobName,
        [string]$AijExec = $TransitPipelineDefaults.AijExec
    )

    if (-not $JobName) {
        throw "Specify -JobName (e.g., job_raw_Ap2-5_In9-0_Out19-0)"
    }

    $macroPath = Join-Path $JobsDir "$JobName.ijm"
    if (-not (Test-Path $macroPath)) {
        throw "Macro not found: $macroPath"
    }
    if (-not (Test-Path $AijExec)) {
        throw "AIJ executable not found: $AijExec"
    }

    Write-Host "[run-macro]" $AijExec "-macro" $macroPath
    & $AijExec -macro $macroPath
}
