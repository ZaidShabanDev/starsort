output "site_url" {
  description = "Open this in the browser: the Angular site, with the API under /api."
  value       = "https://${aws_cloudfront_distribution.site.domain_name}"
}

output "oauth_callback_url" {
  description = "Paste this into the GitHub OAuth App's Authorization callback URL."
  value       = "https://${aws_cloudfront_distribution.site.domain_name}/api/auth/callback"
}

output "health_url" {
  value = "https://${aws_cloudfront_distribution.site.domain_name}/api/health"
}

output "site_bucket" {
  description = "Used by scripts/deploy_frontend.ps1."
  value       = aws_s3_bucket.site.bucket
}

output "site_distribution_id" {
  description = "Used by scripts/deploy_frontend.ps1 to clear the CloudFront cache."
  value       = aws_cloudfront_distribution.site.id
}

output "api_url" {
  description = "API Gateway's own address. Use site_url instead: the login cookie only works there."
  value       = aws_apigatewayv2_api.http.api_endpoint
}
