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
  description = "Monthly AWS spend limit that triggers email alerts. Alerts only, nothing is blocked."
  type        = string
  default     = "5"
}

variable "alert_email" {
  description = "Email address that receives budget alerts."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be a valid email address."
  }
}
