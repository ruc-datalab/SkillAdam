$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$installer = Join-Path (Split-Path -Parent $scriptDir) "install.py"

if ($env:SKILLADAM_INSTALL_PYTHON) {
    & $env:SKILLADAM_INSTALL_PYTHON $installer github-copilot @args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python $installer github-copilot @args
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $installer github-copilot @args
} else {
    throw "Python 3 was not found."
}
exit $LASTEXITCODE
