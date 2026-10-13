<# Relative source-checkout wrapper. Publish means copying a draft into the editor. #>
param(
    [Parameter(Mandatory = $true)][string] $Source,
    [string] $Name,
    [string] $Goal,
    [switch] $Publish,
    [switch] $SkipTranscribe,
    [switch] $ExportMp4,
    [switch] $Force
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python -ErrorAction Stop).Source }
$env:PYTHONIOENCODING = 'utf-8'
if ($Force) { throw 'The pipeline requires quality gates. Fix the task report and retry.' }
$inputItem = Get-Item -LiteralPath $Source
$files = if ($inputItem.PSIsContainer) {
    @(Get-ChildItem -LiteralPath $inputItem.FullName -File -Recurse | Where-Object {
        $_.Extension.ToLower() -in @('.mp4','.mov','.mkv','.avi','.flv','.wmv','.m4v','.mpg','.mpeg','.webm')
    })
} else { @($inputItem) }
if ($files.Count -eq 0) { throw 'No video files found' }
if ($Name -and $files.Count -gt 1) { throw 'Do not pass Name for batch inputs' }
foreach ($file in $files) {
    $pipelineArgs = @((Join-Path $root 'scripts\pipeline.py'), '--video', $file.FullName, '--semantic')
    if ($Name) { $pipelineArgs += @('--name', $Name) }
    if ($Goal) { $pipelineArgs += @('--goal', $Goal) }
    if ($Publish) { $pipelineArgs += '--publish' }
    if ($SkipTranscribe) { $pipelineArgs += '--skip-transcribe' }
    if ($ExportMp4) { $pipelineArgs += '--export-mp4' }
    & $py @pipelineArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
exit 0
