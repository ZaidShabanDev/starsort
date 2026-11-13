output "api_url" {
  value = aws_apigatewayv2_api.http.api_endpoint
}

output "health_url" {
  value = "${aws_apigatewayv2_api.http.api_endpoint}/health"
}
