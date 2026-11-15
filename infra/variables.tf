variable "aws_region" {
  description = "Region where starsort calls Bedrock. us-west-2 is the Strands default."
  type        = string
  default     = "us-west-2"
}

variable "iam_user_name" {
  description = "IAM user that starsort runs as. It can only call Bedrock models."
  type        = string
  default     = "starsort"
}

variable "monthly_budget_usd" {
  description = "Monthly AWS spend limit that triggers email alerts. Alerts only, nothing is blocked. Just above the web app's $50 hard stop (infra/web monthly_spend_cap_usd)."
  type        = string
  default     = "55"
}

variable "alert_email" {
  description = "Email address that receives budget alerts."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be a valid email address."
  }
}
