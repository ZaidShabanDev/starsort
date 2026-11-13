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

variable "throttle_burst" {
  description = "Short burst limit for the whole API."
  type        = number
  default     = 20
}
