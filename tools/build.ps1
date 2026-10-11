[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$taskVenvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$taskOutputDirectory = [IO.Path]::GetFullPath((Join-Path $projectRoot 'dist\AutoZZZ'))
$taskBackupDirectory = $null

# PyInstaller replaces its output recursively. Validate that output stays in this project.
if (-not $taskOutputDirectory.StartsWith($projectRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $taskOutputDirectory -ne (Join-Path $projectRoot 'dist\AutoZZZ')) {
    throw 'The resolved build output is outside the intended AutoZZZ directory.'
}
foreach ($taskRequiredName in @('.venv\Scripts\python.exe', 'launcher.py', 'LICENSE', 'requirements.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $taskRequiredName) -PathType Leaf)) {
        throw "Required file was not found: $taskRequiredName. Run setup.ps1 first."
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'assets') -PathType Container)) {
    throw 'The assets directory is missing.'
}

Push-Location -LiteralPath $projectRoot
try {
    foreach ($taskSavedName in @('config.json', 'runs')) {
        $taskSavedPath = Join-Path $taskOutputDirectory $taskSavedName
        if (Test-Path -LiteralPath $taskSavedPath) {
            if (-not $taskBackupDirectory) {
                $taskBackupDirectory = Join-Path $projectRoot ('build\userdata-' + (Get-Date -Format 'yyyyMMdd_HHmmss_ffff'))
                New-Item -ItemType Directory -Path $taskBackupDirectory -Force | Out-Null
            }
            Copy-Item -LiteralPath $taskSavedPath -Destination $taskBackupDirectory -Recurse
        }
    }
    & $taskVenvPython -c 'import importlib.util, sys; sys.exit(0 if importlib.util.find_spec(''PyInstaller'') else 1)'
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Installing PyInstaller into the AutoZZZ virtual environment...'
        & $taskVenvPython -m pip install --disable-pip-version-check 'PyInstaller>=6,<7'
        if ($LASTEXITCODE -ne 0) { throw 'PyInstaller installation failed.' }
    }
    & $taskVenvPython -m PyInstaller --noconfirm --onedir --windowed --uac-admin --name AutoZZZ `
        --collect-all rapidocr_onnxruntime --collect-all onnxruntime `
        --add-data 'assets;assets' --add-data 'LICENSE;.' launcher.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed. See output above.' }
    Copy-Item -LiteralPath (Join-Path $projectRoot 'LICENSE') -Destination (Join-Path $taskOutputDirectory 'LICENSE') -Force
    foreach ($taskDocument in @('README.md', 'DAILY_FLOW.md', 'VALIDATION.md', 'LICENSE_NOTICE.md')) {
        $taskDocumentPath = Join-Path $projectRoot $taskDocument
        if (Test-Path -LiteralPath $taskDocumentPath -PathType Leaf) {
            $taskDocumentText = (Get-Content -LiteralPath $taskDocumentPath -Raw -Encoding UTF8).Replace('](assets/', '](_internal/assets/').Replace('](runs/', '](../../runs/').Replace('](dist/AutoZZZ/runs/', '](runs/')
            Set-Content -LiteralPath (Join-Path $taskOutputDirectory $taskDocument) -Value $taskDocumentText -Encoding UTF8
        }
    }
    Write-Host "Build complete: $(Join-Path $taskOutputDirectory 'AutoZZZ.exe')"
} finally {
    if ($taskBackupDirectory) {
        New-Item -ItemType Directory -Path $taskOutputDirectory -Force | Out-Null
        # Restore files individually to avoid nesting an existing runs directory.
        $taskBackupPrefix = $taskBackupDirectory.TrimEnd('\') + '\'
        foreach ($taskSavedItem in Get-ChildItem -LiteralPath $taskBackupDirectory -Recurse -Force) {
            $taskRelativePath = $taskSavedItem.FullName.Substring($taskBackupPrefix.Length)
            $taskRestorePath = Join-Path $taskOutputDirectory $taskRelativePath
            if ($taskSavedItem.PSIsContainer) {
                New-Item -ItemType Directory -Path $taskRestorePath -Force | Out-Null
            } else {
                New-Item -ItemType Directory -Path (Split-Path -Parent $taskRestorePath) -Force | Out-Null
                Copy-Item -LiteralPath $taskSavedItem.FullName -Destination $taskRestorePath -Force
            }
        }
        Write-Host "Configuration and run evidence restored. Recovery copy: $taskBackupDirectory"
    }
    Pop-Location
}
