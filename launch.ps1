param([string]$Document, [string]$Python)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\resolve_python.ps1')
$taskPython = Resolve-SimpleBoardPython -Python $Python -ProjectRoot $PSScriptRoot
$taskDocument = if ($Document) { $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Document) } else { $null }
$taskExitCode = 0
Push-Location $PSScriptRoot
try {
    if ($taskDocument) { & $taskPython -B main.py $taskDocument }
    else { & $taskPython -B main.py }
    $taskExitCode = $LASTEXITCODE
} finally { Pop-Location }
exit $taskExitCode
