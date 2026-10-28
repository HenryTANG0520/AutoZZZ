param([string]$UvPath = "uv")
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$sourceRoot = Join-Path $taskRoot 'vendor\onedragon'
Push-Location $taskRoot
try {
    git submodule update --init --recursive -- vendor/onedragon
    if ($LASTEXITCODE -ne 0) { throw 'OneDragon submodule initialization failed.' }
    $revision = git -C $sourceRoot rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or $revision -ne 'f80b68456306d2afb97be72436dcda27f2ed6b59') {
        throw 'OneDragon source does not match the revision pinned by AutoZZZ.'
    }
    if (-not (Get-Command $UvPath -ErrorAction SilentlyContinue)) {
        throw 'Install uv first, or pass the uv.exe path using -UvPath.'
    }
    Push-Location $sourceRoot
    try {
        & $UvPath sync --frozen --python 3.11 --no-default-groups
        if ($LASTEXITCODE -ne 0) { throw 'OneDragon environment installation failed.' }
    } finally { Pop-Location }
    Write-Output 'OneDragon source and Python 3.11 environment are ready. No game was started.'
} finally { Pop-Location }
