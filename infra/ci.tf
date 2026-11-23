# GitHub Actions deploys infra/web. Everything here stays manual, so the pipeline can never change its own permissions.

data "aws_caller_identity" "current" {}

locals {
  account_id     = data.aws_caller_identity.current.account_id
  web_state_key  = "web/terraform.tfstate"
  web_boundary   = "${var.web_name_prefix}-boundary"
  web_log_groups = "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/lambda/${var.web_name_prefix}-*"
  github_subjects = [
    "repo:${var.github_owner}/${var.github_repo}:environment:${var.github_environment}",
    # Repos created after July 2026 get IDs in the claim (GitHub's immutable subject format).
    "repo:${var.github_owner}@${var.github_owner_id}/${var.github_repo}@${var.github_repo_id}:environment:${var.github_environment}",
  ]
}

# ---------- Terraform state for infra/web, shared by this PC and GitHub Actions ----------

resource "aws_s3_bucket" "tfstate" {
  bucket = "starsort-tfstate-${local.account_id}"

  lifecycle {
    prevent_destroy = true
  }
}

# Every apply keeps the old state as a version, so a bad apply can be rolled back.
resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------- Permissions boundary: the most any infra/web Lambda role can ever do ----------

# Keep in sync with the Lambda policies in infra/web/main.tf: a permission missing here is denied even if the role grants it.
data "aws_iam_policy_document" "web_boundary" {
  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${local.web_log_groups}:*"]
  }

  statement {
    sid       = "WebTables"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = ["arn:aws:dynamodb:${var.aws_region}:${local.account_id}:table/${var.web_name_prefix}-*"]
  }

  statement {
    sid       = "InvokeWebFunctions"
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:aws:lambda:${var.aws_region}:${local.account_id}:function:${var.web_name_prefix}-*"]
  }

  statement {
    sid       = "ReadWebSecrets"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.aws_region}:${local.account_id}:parameter${var.web_ssm_prefix}/*"]
  }

  statement {
    sid       = "DecryptViaSsm"
    actions   = ["kms:Decrypt"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "InvokeBedrockModels"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "web_boundary" {
  name        = local.web_boundary
  description = "Permissions boundary for the starsort web Lambda roles"
  policy      = data.aws_iam_policy_document.web_boundary.json
}

# ---------- GitHub Actions deploy role (OIDC, no stored AWS keys) ----------

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "github_deploy_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Only jobs in the approved environment of this one repo.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.github_subjects
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name                 = "starsort-github-deploy"
  description          = "Assumed by GitHub Actions to deploy infra/web"
  assume_role_policy   = data.aws_iam_policy_document.github_deploy_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "github_deploy" {
  statement {
    sid       = "StateBucketList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.tfstate.arn]
  }

  statement {
    sid       = "StateFile"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.tfstate.arn}/${local.web_state_key}"]
  }

  statement {
    sid       = "StateLockFile"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.tfstate.arn}/${local.web_state_key}.tflock"]
  }

  statement {
    sid     = "WebLambdas"
    actions = ["lambda:*"]
    resources = [
      "arn:aws:lambda:${var.aws_region}:${local.account_id}:function:${var.web_name_prefix}-*",
      "arn:aws:lambda:${var.aws_region}:${local.account_id}:layer:${var.web_name_prefix}-*",
    ]
  }

  statement {
    sid       = "WebTables"
    actions   = ["dynamodb:*"]
    resources = ["arn:aws:dynamodb:${var.aws_region}:${local.account_id}:table/${var.web_name_prefix}-*"]
  }

  statement {
    sid       = "WebLogGroups"
    actions   = ["logs:*"]
    resources = [local.web_log_groups, "${local.web_log_groups}:*"]
  }

  statement {
    sid       = "WebSiteBucket"
    actions   = ["s3:*"]
    resources = ["arn:aws:s3:::${var.web_name_prefix}-site-*"]
  }

  statement {
    sid     = "WebHttpApi"
    actions = ["apigateway:*"]
    resources = [
      "arn:aws:apigateway:${var.aws_region}::/apis",
      "arn:aws:apigateway:${var.aws_region}::/apis/*",
      "arn:aws:apigateway:${var.aws_region}::/tags/*",
    ]
  }

  # Distribution ARNs use random IDs, so they can't be limited by name.
  statement {
    sid     = "WebCloudFront"
    actions = ["cloudfront:*"]
    resources = [
      "arn:aws:cloudfront::${local.account_id}:distribution/*",
      "arn:aws:cloudfront::${local.account_id}:origin-access-control/*",
      "arn:aws:cloudfront::${local.account_id}:function/${var.web_name_prefix}-*",
    ]
  }

  statement {
    sid = "ReadOnlyLookups"
    actions = [
      "cloudfront:Get*",
      "cloudfront:List*",
      "kms:DescribeKey",
      "kms:ListAliases",
      "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }

  statement {
    sid = "ManageWebRoles"
    actions = [
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:DeleteRolePolicy",
      "iam:DeleteRole",
    ]
    resources = ["arn:aws:iam::${local.account_id}:role/${var.web_name_prefix}-*"]
  }

  # Roles can only be created or given permissions while the boundary is attached, so the pipeline can't make an admin role.
  statement {
    sid       = "GrantOnlyWithinBoundary"
    actions   = ["iam:CreateRole", "iam:PutRolePolicy", "iam:PutRolePermissionsBoundary"]
    resources = ["arn:aws:iam::${local.account_id}:role/${var.web_name_prefix}-*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.web_boundary.arn]
    }
  }

  statement {
    sid       = "PassWebRolesToLambda"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.account_id}:role/${var.web_name_prefix}-*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com"]
    }
  }

  statement {
    sid       = "KeepBoundary"
    effect    = "Deny"
    actions   = ["iam:DeleteRolePermissionsBoundary"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "deploy-web"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.github_deploy.json
}
