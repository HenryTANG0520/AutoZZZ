[CmdletBinding()]
param([switch]$StageOnly, [switch]$NoAdmin, [string]$PublishStagedPackage)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$taskVenvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$taskOutputDirectory = [IO.Path]::GetFullPath((Join-Path $projectRoot 'dist\AutoZZZ'))
$taskBuildRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'build'))
$taskStagingRoot = Join-Path $taskBuildRoot ('package-' + [guid]::NewGuid().ToString('N'))
$taskStagingOutput = Join-Path $taskStagingRoot 'AutoZZZ'
if ($PublishStagedPackage) {
    if ($StageOnly -or $NoAdmin) { throw 'PublishStagedPackage cannot change the existing package build mode.' }
    $taskStagingOutput = [IO.Path]::GetFullPath($PublishStagedPackage)
    $taskStagingRoot = Split-Path -Parent $taskStagingOutput
    if ((Split-Path -Leaf $taskStagingOutput) -ne 'AutoZZZ') { throw 'The completed staging directory must be named AutoZZZ.' }
}
$taskArchive = Join-Path $taskBuildRoot ('previous-package-' + [guid]::NewGuid().ToString('N'))

if ($taskOutputDirectory -ne (Join-Path $projectRoot 'dist\AutoZZZ')) {
    throw 'The resolved package destination is outside the intended AutoZZZ directory.'
}
foreach ($taskCheckedPath in @($taskStagingRoot, $taskStagingOutput, $taskArchive)) {
    if (-not ([IO.Path]::GetFullPath($taskCheckedPath)).StartsWith($taskBuildRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The resolved staging or archive directory is outside this project build directory.'
    }
}
foreach ($taskRequiredName in @('.venv\Scripts\python.exe', 'launcher.py', 'LICENSE', 'requirements.txt', 'tools\package_onedragon_runtime.py', 'tools\package_release.py')) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $taskRequiredName) -PathType Leaf)) {
        throw "Required file was not found: $taskRequiredName. Run setup.ps1 first."
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'assets') -PathType Container)) {
    throw 'The assets directory is missing.'
}

Push-Location -LiteralPath $projectRoot
try {
    if ($PublishStagedPackage) {
        if (-not (Test-Path -LiteralPath (Join-Path $taskStagingOutput 'AutoZZZ.exe') -PathType Leaf)) { throw 'The completed staged executable is missing.' }
        & $taskVenvPython -X utf8 -c 'from pathlib import Path; import sys; from autozzz.program_package import verify_package; from autozzz.onedragon_runtime import UPSTREAM_REVISION, PROTOCOL_VERSION; result = verify_package(Path(sys.argv[1])); assert result[''runtime_revision''] == UPSTREAM_REVISION and result[''protocol''] == PROTOCOL_VERSION' $taskStagingOutput
        if ($LASTEXITCODE -ne 0) { throw 'The completed staged runtime did not pass verification.' }
    } else {
        & $taskVenvPython -c 'import importlib.util, sys; sys.exit(0 if importlib.util.find_spec(''PyInstaller'') else 1)'
        if ($LASTEXITCODE -ne 0) { throw 'PyInstaller is missing. Build stopped; no dependencies installed.' }
        $taskPyInstallerArguments = @('-m', 'PyInstaller', '--noconfirm', '--onedir', '--windowed', '--name', 'AutoZZZ',
            '--distpath', $taskStagingRoot, '--workpath', (Join-Path $taskStagingRoot 'analysis'),
            '--collect-all', 'rapidocr_onnxruntime', '--collect-all', 'onnxruntime',
            '--add-data', 'assets;assets', '--add-data', 'LICENSE;.', 'launcher.py')
        if (-not $NoAdmin) { $taskPyInstallerArguments += '--uac-admin' }
        & $taskVenvPython @taskPyInstallerArguments
        if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed. See output above.' }
        & $taskVenvPython -X utf8 -u tools/package_onedragon_runtime.py --output (Join-Path $taskStagingOutput 'onedragon')
        if ($LASTEXITCODE -ne 0) { throw 'The fixed OneDragon runtime could not be packaged. Existing installation is unchanged.' }
        Copy-Item -LiteralPath (Join-Path $projectRoot 'LICENSE') -Destination (Join-Path $taskStagingOutput 'LICENSE')
        foreach ($taskDocument in @('README.md', 'DAILY_FLOW.md', 'BATTLE.md', 'VALIDATION.md', 'LICENSE_NOTICE.md', 'ONEDRAGON_INTEGRATION.md', 'SCHEDULING.md')) {
            $taskDocumentPath = Join-Path $projectRoot $taskDocument
            if (Test-Path -LiteralPath $taskDocumentPath -PathType Leaf) {
                $taskDocumentText = (Get-Content -LiteralPath $taskDocumentPath -Raw -Encoding UTF8).Replace('](assets/', '](_internal/assets/').Replace('](runs/', '](../../runs/').Replace('](dist/AutoZZZ/runs/', '](runs/')
                Set-Content -LiteralPath (Join-Path $taskStagingOutput $taskDocument) -Value $taskDocumentText -Encoding UTF8
            }
        }
        $taskPreviewSource = Join-Path $projectRoot 'docs\images'
        if (Test-Path -LiteralPath $taskPreviewSource -PathType Container) {
            New-Item -ItemType Directory -Path (Join-Path $taskStagingOutput 'docs') | Out-Null
            Copy-Item -LiteralPath $taskPreviewSource -Destination (Join-Path $taskStagingOutput 'docs') -Recurse
        }
        $taskBuildRevision = (& git rev-parse --short HEAD).Trim()
        if ($LASTEXITCODE -ne 0) { throw 'Cannot read the AutoZZZ build revision.' }
        & $taskVenvPython -X utf8 (Join-Path $projectRoot 'tools\package_release.py') --package $taskStagingOutput --version ('dev-' + $taskBuildRevision)
        if ($LASTEXITCODE -ne 0) { throw 'The complete package manifest could not be written.' }
        Write-Host "Staged package: $taskStagingOutput"
        if ($StageOnly) { return }
    }

    # Publish the complete directory. Never mix a new runtime with a running old EXE.
    $taskProcesses = @(Get-CimInstance Win32_Process | Where-Object {
        ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($taskOutputDirectory + '\', [StringComparison]::OrdinalIgnoreCase)) -or
        ($_.Name -eq 'AutoZZZ.exe' -and -not $_.ExecutablePath)
    })
    if ($taskProcesses.Count) { throw "AutoZZZ is running. Close it before publishing. The completed package remains at $taskStagingOutput" }
    $taskExecutableLock = $null
    try {
        $taskOldExecutable = Join-Path $taskOutputDirectory 'AutoZZZ.exe'
        if (Test-Path -LiteralPath $taskOldExecutable -PathType Leaf) {
            # Deny late starts during user-data copying. Windows blocks renaming
            # the parent directory until this file handle has been released.
            $taskExecutableLock = [IO.File]::Open($taskOldExecutable, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::Delete)
        }
        if (Test-Path -LiteralPath $taskOutputDirectory -PathType Container) {
            & $taskVenvPython -X utf8 (Join-Path $projectRoot 'tools\package_release.py') --package $taskStagingOutput --preserve-from $taskOutputDirectory
            if ($LASTEXITCODE -ne 0) { throw 'User data could not be preserved. Existing installation is unchanged.' }
        }
        New-Item -ItemType Directory -Path (Split-Path -Parent $taskOutputDirectory) -Force | Out-Null
        if ($taskExecutableLock) { $taskExecutableLock.Dispose(); $taskExecutableLock = $null }
        $taskLateProcesses = @(Get-CimInstance Win32_Process | Where-Object {
            ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($taskOutputDirectory + '\', [StringComparison]::OrdinalIgnoreCase)) -or
            ($_.Name -eq 'AutoZZZ.exe' -and -not $_.ExecutablePath)
        })
        if ($taskLateProcesses.Count) { throw "AutoZZZ started while user data was being copied. Close it and publish the completed package at $taskStagingOutput" }
        # Same-volume directory rename: the Windows PowerShell provider's Move-Item
        # can enumerate and partially move a tree when a deep Qt path exceeds MAX_PATH.
        if (Test-Path -LiteralPath $taskOutputDirectory) { [IO.Directory]::Move($taskOutputDirectory, $taskArchive) }
        try { [IO.Directory]::Move($taskStagingOutput, $taskOutputDirectory) }
        catch {
            if ((Test-Path -LiteralPath $taskArchive) -and -not (Test-Path -LiteralPath $taskOutputDirectory)) {
                [IO.Directory]::Move($taskArchive, $taskOutputDirectory)
            }
            throw
        }
    } finally {
        if ($taskExecutableLock) { $taskExecutableLock.Dispose() }
    }
    Write-Host "Build complete: $(Join-Path $taskOutputDirectory 'AutoZZZ.exe')"
    if (Test-Path -LiteralPath $taskArchive) { Write-Host "Previous complete package and user data: $taskArchive" }
} finally { Pop-Location }
