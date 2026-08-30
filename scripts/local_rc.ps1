param(
    [ValidateSet("bootstrap", "start", "stop", "restart", "status")]
    [string]$Action = "status",
    [string]$EnvFile = ""
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Find-LocalEnvFile {
    if ($EnvFile) {
        return (Resolve-Path -LiteralPath $EnvFile).Path
    }
    $localEnv = Join-Path $RepositoryRoot ".env"
    if (Test-Path -LiteralPath $localEnv) {
        return $localEnv
    }
    $worktrees = git -C $RepositoryRoot worktree list --porcelain
    foreach ($line in $worktrees) {
        if ($line.StartsWith("worktree ")) {
            $candidate = Join-Path $line.Substring(9) ".env"
            if (Test-Path -LiteralPath $candidate) {
                return $candidate
            }
        }
    }
    throw "No ignored .env was found in this repository's worktrees."
}

function Import-LocalEnv([string]$Path) {
    foreach ($rawLine in Get-Content -LiteralPath $Path) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            continue
        }
        $parts = $line.Split("=", 2)
        $name = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

function Set-RcEnvironment {
    Import-LocalEnv (Find-LocalEnvFile)
    if ([string]::IsNullOrWhiteSpace($env:DEEPSEEK_API_KEY)) {
        throw "DEEPSEEK_API_KEY is required for local Agent use."
    }
    if ([string]::IsNullOrWhiteSpace($env:JWT_SECRET) -or $env:JWT_SECRET.Length -lt 32) {
        throw "JWT_SECRET must contain at least 32 characters."
    }
    $material = [Text.Encoding]::UTF8.GetBytes($env:JWT_SECRET)
    $digest = [Security.Cryptography.SHA256]::HashData($material)
    $env:RC_POSTGRES_PASSWORD = ([Convert]::ToHexString($digest)).Substring(0, 32).ToLowerInvariant()
    $env:RELEASE_SHA = (git -C $RepositoryRoot rev-parse HEAD).Trim()
}

Set-RcEnvironment
$compose = @("compose", "-f", (Join-Path $RepositoryRoot "compose.rc.yaml"))

switch ($Action) {
    "bootstrap" { docker @compose --profile local-bootstrap run --build --rm local-bootstrap }
    "start" { docker @compose up --build -d --wait frontend-v2 }
    "stop" { docker @compose down }
    "restart" { docker @compose restart backend frontend-v2 }
    "status" {
        docker @compose ps
        Invoke-RestMethod "http://127.0.0.1:$($env:RC_HTTP_PORT ?? '8080')/ready" |
            ConvertTo-Json -Depth 6
    }
}

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
