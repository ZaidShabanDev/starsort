output "iam_user_name" {
  value = aws_iam_user.starsort.name
}

output "budget_name" {
  value = aws_budgets_budget.monthly.name
}

output "next_step" {
  value = "aws iam create-access-key --user-name ${aws_iam_user.starsort.name}   then   aws configure --profile starsort"
}
