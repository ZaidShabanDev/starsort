# CloudWatch alarms that email me. The SNS topic and its email subscription live in infra/ci.tf, so my address stays out of CI.

locals {
  alerts_topic_arn = "arn:aws:sns:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${var.alerts_topic_name}"

  lambda_alarms = {
    worker-errors = {
      function    = aws_lambda_function.worker.function_name
      metric      = "Errors"
      statistic   = "Sum"
      threshold   = 1
      description = "A worker run crashed or timed out (errors the code did not catch)."
    }
    worker-near-timeout = {
      function    = aws_lambda_function.worker.function_name
      metric      = "Duration"
      statistic   = "Maximum"
      threshold   = 13 * 60 * 1000 # ms; Lambda stops the worker at 15 minutes
      description = "A worker run took over 13 minutes, close to Lambda's 15-minute limit."
    }
    worker-dropped-runs = {
      function    = aws_lambda_function.worker.function_name
      metric      = "AsyncEventsDropped"
      statistic   = "Sum"
      threshold   = 1
      description = "A queued run was dropped before it started, so a user waited for nothing."
    }
    api-errors = {
      function    = aws_lambda_function.api.function_name
      metric      = "Errors"
      statistic   = "Sum"
      threshold   = 5
      description = "The API failed 5 or more times in 5 minutes."
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "lambda" {
  for_each = local.lambda_alarms

  alarm_name          = "${var.name_prefix}-${each.key}"
  alarm_description   = each.value.description
  namespace           = "AWS/Lambda"
  metric_name         = each.value.metric
  dimensions          = { FunctionName = each.value.function }
  statistic           = each.value.statistic
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = each.value.threshold
  treat_missing_data  = "notBreaching" # no runs means nothing is wrong
  alarm_actions       = [local.alerts_topic_arn]
  ok_actions          = [local.alerts_topic_arn]
}

# The worker catches its own errors (so Lambda never retries a paid run), so they don't show in the Errors metric. Count its ERROR logs instead.
resource "aws_cloudwatch_log_metric_filter" "worker_error_logs" {
  name           = "${var.name_prefix}-worker-error-logs"
  log_group_name = aws_cloudwatch_log_group.worker.name
  pattern        = "{ $.level = \"ERROR\" }"

  metric_transformation {
    name      = "WorkerErrorLogs"
    namespace = "Starsort"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "worker_error_logs" {
  alarm_name          = "${var.name_prefix}-worker-failed-runs"
  alarm_description   = "A run or Lists sync failed and logged an error."
  namespace           = "Starsort"
  metric_name         = "WorkerErrorLogs"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [local.alerts_topic_arn]
  ok_actions          = [local.alerts_topic_arn]

  depends_on = [aws_cloudwatch_log_metric_filter.worker_error_logs]
}
