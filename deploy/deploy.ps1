<#
.SYNOPSIS
    Windows wrapper for deploy/deploy.sh — the real logic stays in bash.

.DESCRIPTION
    deploy.sh needs bash (tar/ssh/comm pipeline). This just finds Git Bash
    and forwards args to it, so `deploy\deploy.ps1 --logs` etc. work from
    PowerShell without a manual `bash deploy/deploy.sh ...` each time.

.EXAMPLE
    deploy\deploy.ps1
    deploy\deploy.ps1 --unit --logs
    deploy\deploy.ps1 --dry-run
#>
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$bash = Get-Command bash.exe -ErrorAction SilentlyContinue
if (-not $bash) {
    $candidate = "$env:ProgramFiles\Git\bin\bash.exe"
    if (Test-Path $candidate) { $bash = Get-Item $candidate }
}
if (-not $bash) {
    Write-Error "deploy: no bash.exe found (need Git for Windows)"
    exit 1
}

$repoRoot = git rev-parse --show-toplevel 2>$null
if (-not $repoRoot) {
    Write-Error "deploy: not inside a git repository"
    exit 1
}

& $bash.Source "deploy/deploy.sh" @Args
exit $LASTEXITCODE
