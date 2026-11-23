output "iam_user_name" {
  value = aws_iam_user.starsort.name
}

output "budget_name" {
  value = aws_budgets_budget.monthly.name
}

output "tfstate_bucket" {
  description = "GitHub secret TF_STATE_BUCKET, and -backend-config for infra/web."
  value       = aws_s3_bucket.tfstate.bucket
}

output "github_deploy_role_arn" {
  description = "GitHub secret AWS_DEPLOY_ROLE_ARN."
  value       = aws_iam_role.github_deploy.arn
}

output "next_step" {
  value = "aws iam create-access-key --user-name ${aws_iam_user.starsort.name}   then   aws configure --profile starsort"
}
