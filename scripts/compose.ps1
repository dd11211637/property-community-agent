param(
    [Parameter(Position = 0)]
    [ValidateSet("Up", "Test", "Reset", "Down", "Config")]
    [string]$Action = "Up"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$junctionPath = Join-Path $env:SystemDrive "codex-property-agent"

if (Test-Path -LiteralPath $junctionPath) {
    $item = Get-Item -LiteralPath $junctionPath -Force
    $target = [string]$item.Target
    if ($item.LinkType -ne "Junction" -or $target -ne $repoRoot) {
        throw "Refusing to reuse $junctionPath because it is not a junction to $repoRoot."
    }
}
else {
    New-Item -ItemType Junction -Path $junctionPath -Target $repoRoot | Out-Null
}

$composeArgs = switch ($Action) {
    "Up" { @("compose", "up", "--build", "-d") }
    "Test" { @("compose", "--profile", "testing", "run", "--build", "--rm", "postgres-tests") }
    "Reset" { @("compose", "--profile", "tools", "run", "--build", "--rm", "reset") }
    "Down" { @("compose", "down") }
    "Config" { @("compose", "config", "--quiet") }
}

Push-Location $junctionPath
try {
    & docker @composeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($composeArgs -join ' ') failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
