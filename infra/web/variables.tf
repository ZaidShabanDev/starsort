variable "aws_region" {
  description = "Region for the hosted web app. Same as Bedrock, so the worker calls it locally."
  type        = string
  default     = "us-west-2"
}

variable "name_prefix" {
  description = "Prefix for every resource name."
  type        = string
  default     = "starsort-web"
}

variable "log_retention_days" {
  description = "How long Lambda logs are kept."
  type        = number
  default     = 14
}

variable "throttle_rate_per_second" {
  description = "Steady request limit for the whole API. Protects the free trial from floods."
  type        = number
  default     = 10
}

variable "github_client_id" {
  description = "Client ID of the GitHub OAuth App. Not a secret: it appears in every login URL."
  type        = string
  default     = "Ov23liCphMnLTWY3CyEb"
}

variable "ssm_prefix" {
  description = "Parameter Store path for the web app's secrets. They are created with the AWS CLI so they never enter Terraform state."
  type        = string
  default     = "/starsort/web"
}

variable "bedrock_model_id" {
  description = "Bedrock model the worker sorts stars with. The account has a model agreement for Sonnet 4.6."
  type        = string
  default     = "us.anthropic.claude-sonnet-4-6"
}

variable "bedrock_input_usd_per_million" {
  description = "Price per million input tokens, used to count spend. Geo (us.) cross-region price, about 10% above global; check the Bedrock pricing page."
  type        = number
  default     = 3.3
}

variable "bedrock_output_usd_per_million" {
  description = "Price per million output tokens, used to count spend. Geo (us.) cross-region price, about 10% above global; check the Bedrock pricing page."
  type        = number
  default     = 16.5
}

variable "monthly_spend_cap_usd" {
  description = "Free-trial hard stop: no new jobs once this month's counted AI spend reaches this amount."
  type        = number
  default     = 50
}

variable "worker_reserved_concurrency" {
  description = "Most worker runs at the same time, so spend can only go a little over the cap. -1 = no limit (needed while the account's Lambda concurrency quota is 10; reserving needs at least 100 left unreserved)."
  type        = number
  default     = 3
}

variable "unlimited_logins" {
  description = "GitHub logins that skip the 1-run-per-week limit (for testing). The spend cap still applies."
  type        = list(string)
  default     = ["ZaidShabanDev"]
}

variable "throttle_burst" {
  description = "Short burst limit for the whole API."
  type        = number
  default     = 20
}
