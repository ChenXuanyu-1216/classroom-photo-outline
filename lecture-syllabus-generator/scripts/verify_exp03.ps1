$ErrorActionPreference = 'Stop'
$applicationDirectory = Split-Path -Parent $PSScriptRoot
$repositoryDirectory = Split-Path -Parent $applicationDirectory
$backendDirectory = Join-Path $applicationDirectory 'backend'
$projectPython = Join-Path $backendDirectory '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $projectPython)) {
    throw '请先按 README 创建 backend/.venv 并安装运行与测试依赖。'
}
$nodeCommand = Get-Command node -ErrorAction Stop

Push-Location $backendDirectory
try {
    & $projectPython -m pytest -q
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally { Pop-Location }

Push-Location $repositoryDirectory
try {
    & $nodeCommand.Source --test 'lecture-syllabus-generator/frontend/tests/*.test.cjs'
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally { Pop-Location }

Write-Host 'EXP03 工程自动化检查通过；真实模型质量与课堂试用不在本检查范围内。'
