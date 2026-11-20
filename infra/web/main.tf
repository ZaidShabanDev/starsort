# ---------- API Lambda ----------

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api" {
  name               = "${var.name_prefix}-api"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${var.name_prefix}-api"
  retention_in_days = var.log_retention_days
}

# Logs only to its own log group (least privilege instead of the broad managed policy).
data "aws_iam_policy_document" "api_logs" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api.arn}:*"]
  }
}

resource "aws_iam_role_policy" "api_logs" {
  name   = "write-own-logs"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api_logs.json
}

# ---------- Secrets (SSM Parameter Store) ----------

data "aws_caller_identity" "current" {}

# Exists only after the first SecureString is created, so run the put-parameter commands before apply.
data "aws_kms_alias" "ssm" {
  name = "alias/aws/ssm"
}

data "aws_iam_policy_document" "api_secrets" {
  statement {
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${var.ssm_prefix}/*"]
  }

  statement {
    actions   = ["kms:Decrypt"]
    resources = [data.aws_kms_alias.ssm.target_key_arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "api_secrets" {
  name   = "read-web-secrets"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api_secrets.json
}

data "aws_iam_policy_document" "api_jobs" {
  statement {
    actions   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.jobs.arn]
  }

  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.worker.arn]
  }

  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.usage.arn]
  }

  statement {
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.spend.arn]
  }
}

resource "aws_iam_role_policy" "api_jobs" {
  name   = "create-and-read-jobs"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api_jobs.json
}

# The whole src/ folder, so "starsort.web.api" imports work the same as locally. The worker uses the same zip.
data "archive_file" "api" {
  type        = "zip"
  source_dir  = "${path.module}/../../src"
  excludes    = ["**/__pycache__/**", "**/*.pyc"]
  output_path = "${path.module}/build/api.zip"
}

resource "aws_lambda_function" "api" {
  function_name = "${var.name_prefix}-api"
  role          = aws_iam_role.api.arn
  filename      = data.archive_file.api.output_path
  code_sha256   = data.archive_file.api.output_base64sha256
  handler       = "starsort.web.api.handler"
  runtime       = "python3.13"
  timeout       = 10
  memory_size   = 256

  environment {
    variables = {
      GITHUB_CLIENT_ID           = var.github_client_id
      GITHUB_CLIENT_SECRET_PARAM = "${var.ssm_prefix}/github-client-secret"
      SESSION_SECRET_PARAM       = "${var.ssm_prefix}/session-secret"
      JOBS_TABLE                 = aws_dynamodb_table.jobs.name
      WORKER_FUNCTION            = aws_lambda_function.worker.function_name
      USAGE_TABLE                = aws_dynamodb_table.usage.name
      SPEND_TABLE                = aws_dynamodb_table.spend.name
      MONTHLY_SPEND_CAP_USD      = var.monthly_spend_cap_usd
      UNLIMITED_LOGINS           = join(",", var.unlimited_logins)
      SITE_URL                   = "https://${aws_cloudfront_distribution.site.domain_name}"
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.api.name
  }
}

# ---------- DynamoDB ----------

resource "aws_dynamodb_table" "jobs" {
  name         = "${var.name_prefix}-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_id"

  attribute {
    name = "job_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

# One item per GitHub user: their stars and saved categories, so later runs only send new stars to the AI.
resource "aws_dynamodb_table" "stars" {
  name         = "${var.name_prefix}-stars"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "owner_id"

  attribute {
    name = "owner_id"
    type = "S"
  }
}

# Free trial: one row per GitHub user with their last run start (1 run per week, 1 running job at a time).
resource "aws_dynamodb_table" "usage" {
  name         = "${var.name_prefix}-usage"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "owner_id"

  attribute {
    name = "owner_id"
    type = "S"
  }
}

# Free trial: one row per UTC month ("2026-11") with the AI cost so far, for the monthly hard stop.
resource "aws_dynamodb_table" "spend" {
  name         = "${var.name_prefix}-spend"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "month"

  attribute {
    name = "month"
    type = "S"
  }
}

# ---------- Worker Lambda ----------

# Built by scripts/build_layer.ps1, which must run before plan/apply.
data "archive_file" "layer" {
  type        = "zip"
  source_dir  = "${path.module}/build/layer"
  output_path = "${path.module}/build/layer.zip"
}

resource "aws_lambda_layer_version" "dependencies" {
  layer_name               = "${var.name_prefix}-dependencies"
  description              = "starsort runtime dependencies (httpx, pydantic, strands-agents) from uv.lock"
  filename                 = data.archive_file.layer.output_path
  source_code_hash         = data.archive_file.layer.output_base64sha256
  compatible_runtimes      = ["python3.13"]
  compatible_architectures = ["x86_64"]
}

resource "aws_iam_role" "worker" {
  name               = "${var.name_prefix}-worker"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/aws/lambda/${var.name_prefix}-worker"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "worker" {
  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.worker.arn}:*"]
  }

  statement {
    sid       = "UpdateJobs"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.jobs.arn]
  }

  statement {
    sid       = "StarsCache"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.stars.arn]
  }

  statement {
    sid       = "FreeTrialUsageAndSpend"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.usage.arn, aws_dynamodb_table.spend.arn]
  }

  # Only the GitHub client secret; the worker never needs the session secret.
  statement {
    sid       = "ReadGitHubClientSecret"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${var.ssm_prefix}/github-client-secret"]
  }

  statement {
    sid       = "DecryptViaSsm"
    actions   = ["kms:Decrypt"]
    resources = [data.aws_kms_alias.ssm.target_key_arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "InvokeBedrockModels"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = ["*"] # cross-region inference profiles route to several regions, so model ARNs aren't fixed
  }
}

resource "aws_iam_role_policy" "worker" {
  name   = "run-jobs"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

resource "aws_lambda_function" "worker" {
  function_name = "${var.name_prefix}-worker"
  role          = aws_iam_role.worker.arn
  filename      = data.archive_file.api.output_path
  code_sha256   = data.archive_file.api.output_base64sha256
  handler       = "starsort.web.worker.handler"
  runtime       = "python3.13"
  architectures = ["x86_64"]
  layers        = [aws_lambda_layer_version.dependencies.arn]
  timeout       = 900 # Lambda's maximum; jobs.STALE_SECONDS in the code assumes this
  memory_size   = 512

  # At most this many runs call Bedrock at once; extra runs wait in Lambda's async queue.
  reserved_concurrent_executions = var.worker_reserved_concurrency

  environment {
    variables = {
      JOBS_TABLE                 = aws_dynamodb_table.jobs.name
      STARS_TABLE                = aws_dynamodb_table.stars.name
      GITHUB_CLIENT_ID           = var.github_client_id
      GITHUB_CLIENT_SECRET_PARAM = "${var.ssm_prefix}/github-client-secret"
      STARSORT_MODEL             = var.bedrock_model_id
      USAGE_TABLE                = aws_dynamodb_table.usage.name
      SPEND_TABLE                = aws_dynamodb_table.spend.name
      INPUT_USD_PER_MILLION      = var.bedrock_input_usd_per_million
      OUTPUT_USD_PER_MILLION     = var.bedrock_output_usd_per_million
      MONTHLY_SPEND_CAP_USD      = var.monthly_spend_cap_usd
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.worker.name
  }
}

# No automatic retries: a failed run is marked failed in DynamoDB, and a retry would pay for Bedrock twice.
resource "aws_lambda_function_event_invoke_config" "worker" {
  function_name                = aws_lambda_function.worker.function_name
  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 3600
}

# ---------- HTTP API ----------

resource "aws_apigatewayv2_api" "http" {
  name          = "${var.name_prefix}-http"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

# Every route starts with /api: CloudFront forwards the full path, and sends only /api/* here.
resource "aws_apigatewayv2_route" "health" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "GET /api/health"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_route" "auth" {
  for_each = toset(["GET /api/auth/login", "GET /api/auth/callback", "POST /api/auth/logout", "GET /api/me"])

  api_id    = aws_apigatewayv2_api.http.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_route" "jobs" {
  for_each = toset(["POST /api/jobs", "GET /api/jobs/latest", "GET /api/jobs/{job_id}"])

  api_id    = aws_apigatewayv2_api.http.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

# "$default" is served from the base URL, with no stage name in the path.
resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_rate_limit  = var.throttle_rate_per_second
    throttling_burst_limit = var.throttle_burst
  }
}

resource "aws_lambda_permission" "allow_api_gateway" {
  statement_id  = "AllowInvokeFromHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}

# ---------- Website: S3 (Angular files) + CloudFront (one address for the site and /api/*) ----------

# Private: only this CloudFront distribution can read it (bucket policy below). Files are uploaded by scripts/deploy_frontend.ps1.
resource "aws_s3_bucket" "site" {
  bucket        = "${var.name_prefix}-site-${data.aws_caller_identity.current.account_id}"
  force_destroy = true # it only holds build output, which can always be rebuilt
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "${var.name_prefix}-site"
  description                       = "CloudFront reads the private site bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

data "aws_iam_policy_document" "site_bucket" {
  statement {
    sid       = "AllowCloudFrontReadOnly"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.site.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.site_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.site]
}

# Angular routes like /dashboard are not files in S3, so they get index.html. Not CloudFront error pages: those would also hide /api errors.
resource "aws_cloudfront_function" "spa_routes" {
  name    = "${var.name_prefix}-spa-routes"
  runtime = "cloudfront-js-2.0"
  comment = "Serve index.html for Angular routes (paths without a file extension)"
  publish = true
  code    = <<-EOT
    function handler(event) {
      var request = event.request;
      if (request.uri.indexOf('.') === -1) {
        request.uri = '/index.html';
      }
      return request;
    }
  EOT
}

data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled"
}

# Forwards cookies, query strings and headers, but not Host: API Gateway needs its own host name.
data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

locals {
  site_origin_id = "site-bucket"
  api_origin_id  = "http-api"
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  comment             = "${var.name_prefix}: Angular site and /api/*"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"

  origin {
    origin_id                = local.site_origin_id
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  origin {
    origin_id   = local.api_origin_id
    domain_name = replace(aws_apigatewayv2_api.http.api_endpoint, "https://", "")

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = local.site_origin_id
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_optimized.id

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.spa_routes.arn
    }
  }

  ordered_cache_behavior {
    path_pattern             = "/api/*"
    target_origin_id         = local.api_origin_id
    viewer_protocol_policy   = "https-only"
    allowed_methods          = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods           = ["GET", "HEAD"]
    compress                 = true
    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
      locations        = []
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
