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

  environment {
    variables = {
      JOBS_TABLE                 = aws_dynamodb_table.jobs.name
      STARS_TABLE                = aws_dynamodb_table.stars.name
      GITHUB_CLIENT_ID           = var.github_client_id
      GITHUB_CLIENT_SECRET_PARAM = "${var.ssm_prefix}/github-client-secret"
      STARSORT_MODEL             = var.bedrock_model_id
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

resource "aws_apigatewayv2_route" "health" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "GET /health"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_route" "auth" {
  for_each = toset(["GET /auth/login", "GET /auth/callback", "GET /me"])

  api_id    = aws_apigatewayv2_api.http.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_route" "jobs" {
  for_each = toset(["POST /jobs", "GET /jobs/{job_id}"])

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
