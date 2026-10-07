function Resolve-SimpleBoardPython {
    param([string]$Python, [string]$ProjectRoot)
    # Windows PowerShell 5.1 treats native stderr as ErrorRecords. A rejected
    # interpreter/launcher is an expected probe result, not a terminating error.
    $ErrorActionPreference = 'Continue'
    if ($Python) {
        $taskCandidates = @($Python)
    } else {
        $taskCandidates = @((Join-Path $ProjectRoot '.venv\Scripts\python.exe'))
        if ($env:VIRTUAL_ENV) {
            $taskCandidates += Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe'
        }
        $taskCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
        if ($taskCommand -and $taskCommand.Source -notlike '*\Microsoft\WindowsApps\*') {
            $taskCandidates += $taskCommand.Source
        }
        $taskLauncher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue
        if ($taskLauncher) {
            $taskLaunchedPython = & $taskLauncher.Source -3.11 -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0) { $taskCandidates += $taskLaunchedPython }
        }
    }
    foreach ($taskCandidate in $taskCandidates) {
        if (-not $taskCandidate) { continue }
        $taskResolvedCommand = Get-Command $taskCandidate -CommandType Application -ErrorAction SilentlyContinue
        if (-not $taskResolvedCommand) { continue }
        $taskResolvedPython = $taskResolvedCommand.Source
        $taskProbe = & $taskResolvedPython -c 'import sys; print(sys.executable); sys.exit(0 if sys.version_info[:2] == (3,11) and sys.maxsize > 2**32 else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) { return [string]$taskProbe }
    }
    throw 'Python 3.11 x64 was not found. Create .venv or pass -Python C:\path\to\python.exe.'
}
