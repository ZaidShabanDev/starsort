output "api_url" {
  value = aws_apigatewayv2_api.http.api_endpoint
}

output "health_url" {
  value = "${aws_apigatewayv2_api.http.api_endpoint}/health"
}

output "login_url" {
  value = "${aws_apigatewayv2_api.http.api_endpoint}/auth/login"
}

output "oauth_callback_url" {
  description = "Paste this into the GitHub OAuth App's Authorization callback URL."
  value       = "${aws_apigatewayv2_api.http.api_endpoint}/auth/callback"
}
