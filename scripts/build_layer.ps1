# Builds the worker Lambda's dependencies layer into infra/web/build/layer/python.
# Run from any folder before `terraform apply`, and again whenever uv.lock changes.
# Steps follow https://docs.astral.sh/uv/guides/integration/aws-lambda/ (Linux wheels, even when built on Windows).

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$build = Join-Path $root "infra\web\build"
$target = Join-Path $build "layer\python"
$requirements = Join-Path $build "layer-requirements.txt"

if (Test-Path $target) { Remove-Item -Recurse -Force $target }
New-Item -ItemType Directory -Force $target | Out-Null

uv export --project $root --frozen --no-dev --no-emit-project --no-hashes --no-header --output-file $requirements
if ($LASTEXITCODE -ne 0) { throw "uv export failed" }

uv pip install --no-installer-metadata --no-compile-bytecode --python-platform x86_64-manylinux2014 --python 3.13 --target $target -r $requirements
if ($LASTEXITCODE -ne 0) { throw "uv pip install failed" }

$sizeMb = [math]::Round(((Get-ChildItem $target -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host "Layer ready: $target ($sizeMb MB unzipped; Lambda allows 250 MB for code + layers)"
