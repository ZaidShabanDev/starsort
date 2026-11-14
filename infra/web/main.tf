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

# The whole src/ folder, so "starsort.web.api" imports work the same as locally.
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
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.api.name
  }
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
