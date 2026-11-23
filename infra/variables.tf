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

variable "web_name_prefix" {
  description = "name_prefix of infra/web. The deploy role and the permissions boundary only cover resources with this prefix."
  type        = string
  default     = "starsort-web"
}

variable "web_ssm_prefix" {
  description = "ssm_prefix of infra/web, where the web app's secrets live."
  type        = string
  default     = "/starsort/web"
}

variable "github_owner" {
  description = "GitHub user or organization that owns the repo allowed to deploy."
  type        = string
  default     = "ZaidShabanDev"
}

variable "github_repo" {
  description = "Repository allowed to deploy."
  type        = string
  default     = "starsort"
}

variable "github_owner_id" {
  description = "Numeric ID of github_owner (owner.id from https://api.github.com/repos/OWNER/REPO)."
  type        = string
  default     = "122286390"
}

variable "github_repo_id" {
  description = "Numeric ID of github_repo (id from https://api.github.com/repos/OWNER/REPO)."
  type        = string
  default     = "1449580253"
}

variable "github_environment" {
  description = "GitHub environment whose jobs may assume the deploy role. Its required reviewers approve each deploy."
  type        = string
  default     = "production"
}

variable "alert_email" {
  description = "Email address that receives budget alerts."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be a valid email address."
  }
}
