# Uploads the built Angular site to the S3 bucket and clears the CloudFront cache.
# First: `terraform apply` in infra/web (once), then `ng build` in frontend/. Needs AWS_PROFILE=admin (aws login), or the deploy role in GitHub Actions.

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$infra = Join-Path $root "infra/web"
$dist = Join-Path $root "frontend/dist/starsort-web/browser"

if (-not (Test-Path (Join-Path $dist "index.html"))) { throw "No build found in $dist. Run 'ng build' in frontend first." }

$bucket = terraform -chdir="$infra" output -raw site_bucket
if ($LASTEXITCODE -ne 0) { throw "terraform output site_bucket failed" }
$distribution = terraform -chdir="$infra" output -raw site_distribution_id
if ($LASTEXITCODE -ne 0) { throw "terraform output site_distribution_id failed" }
$siteUrl = terraform -chdir="$infra" output -raw site_url

# Everything is re-checked by the browser (no-cache), except .js/.css: Angular puts a hash in their names, so they can be kept a year.
aws s3 sync $dist "s3://$bucket" --delete --cache-control "no-cache"
if ($LASTEXITCODE -ne 0) { throw "s3 sync failed" }
aws s3 cp $dist "s3://$bucket" --recursive --exclude "*" --include "*.js" --include "*.css" --cache-control "public,max-age=31536000,immutable"
if ($LASTEXITCODE -ne 0) { throw "s3 cp failed" }

$invalidation = aws cloudfront create-invalidation --distribution-id $distribution --paths "/*" --query "Invalidation.Id" --output text
if ($LASTEXITCODE -ne 0) { throw "CloudFront invalidation failed" }

Write-Host "Uploaded to s3://$bucket and cleared the CloudFront cache (invalidation $invalidation, takes about a minute)."
Write-Host "Open: $siteUrl"
