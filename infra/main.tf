resource "aws_iam_user" "starsort" {
  name = var.iam_user_name
}

data "aws_iam_policy_document" "bedrock_invoke" {
  statement {
    sid = "InvokeBedrockModels"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = ["*"] # cross-region inference profiles route to several regions, so model ARNs aren't fixed
  }
}

resource "aws_iam_user_policy" "bedrock_invoke" {
  name   = "starsort-bedrock-invoke"
  user   = aws_iam_user.starsort.name
  policy = data.aws_iam_policy_document.bedrock_invoke.json
}

# The access key is created with the AWS CLI, not here, so its secret never lands in terraform.tfstate.

resource "aws_budgets_budget" "monthly" {
  name         = "starsort-monthly"
  budget_type  = "COST"
  limit_amount = var.monthly_budget_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
