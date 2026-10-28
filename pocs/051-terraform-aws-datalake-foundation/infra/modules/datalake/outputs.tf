output "bucket_names" {
  description = "Data and log bucket names by layer."
  value       = merge({ for k, b in aws_s3_bucket.data : k => b.id }, { logs = aws_s3_bucket.logs.id })
}

output "kms_key_arn" {
  value = aws_kms_key.lake.arn
}

output "role_arns" {
  value = { for k, r in aws_iam_role.lake : k => r.arn }
}
