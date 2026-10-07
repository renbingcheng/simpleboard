param(
    [ValidateSet('onefile', 'onedir')]
    [string]$Mode = 'onefile',
    [string]$Python
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\resolve_python.ps1')
$taskPython = Resolve-SimpleBoardPython -Python $Python -ProjectRoot $PSScriptRoot

function Invoke-SimpleBoardBuildStep {
    param([string[]]$Arguments, [string]$Log)
    # PyInstaller logs to stderr even on success. PS5 must use the exit code.
    $ErrorActionPreference = 'Continue'
    & $taskPython @Arguments 2>&1 | Tee-Object -FilePath $Log
    $taskStepExitCode = $LASTEXITCODE
    if ($taskStepExitCode -ne 0) { throw "Build step failed (exit $taskStepExitCode): $($Arguments -join ' ')" }
}
$taskSavedEnvironment = @{}
foreach ($taskName in @('PATH', 'PYTHONPATH', 'PYTHONHOME', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH',
                        'QML2_IMPORT_PATH', 'QT_QPA_PLATFORM', 'QBOARD_BUILD_MODE')) {
    $taskSavedEnvironment[$taskName] = [Environment]::GetEnvironmentVariable($taskName, 'Process')
}
Push-Location $PSScriptRoot
try {
    $taskBasePython = & $taskPython -c 'import sys; print(sys.base_prefix)'
    if ($LASTEXITCODE -ne 0) { throw '无法读取现有 Python 环境。' }
    $env:PATH = @((Split-Path -Parent $taskPython), $taskBasePython,
                  (Join-Path $env:SystemRoot 'System32'), $env:SystemRoot) -join [IO.Path]::PathSeparator
    foreach ($taskName in @('PYTHONPATH', 'PYTHONHOME', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH',
                            'QML2_IMPORT_PATH', 'QT_QPA_PLATFORM')) {
        [Environment]::SetEnvironmentVariable($taskName, $null, 'Process')
    }
    $env:QBOARD_BUILD_MODE = $Mode
    $taskArtifacts = Join-Path $PSScriptRoot 'artifacts'
    New-Item -ItemType Directory -Path $taskArtifacts -Force | Out-Null
    & $taskPython -B scripts/prepare_licenses.py
    if ($LASTEXITCODE -ne 0) { throw 'Third-party license verification failed.' }
    Invoke-SimpleBoardBuildStep -Arguments @('-m', 'PyInstaller', '--clean', '--noconfirm', 'simpleboard.spec') -Log (Join-Path $taskArtifacts "build-$Mode.log")
    Invoke-SimpleBoardBuildStep -Arguments @('-B', 'scripts/package_portable.py', '--mode', $Mode) -Log (Join-Path $taskArtifacts "package-$Mode.log")
} finally {
    foreach ($taskName in $taskSavedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($taskName, $taskSavedEnvironment[$taskName], 'Process')
    }
    Pop-Location
}
