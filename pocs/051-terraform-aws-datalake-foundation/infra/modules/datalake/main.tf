locals {
  buckets = {
    raw     = "${var.name_prefix}-${var.environment}-${var.account_id}-raw"
    curated = "${var.name_prefix}-${var.environment}-${var.account_id}-curated"
  }
  logs_bucket = "${var.name_prefix}-${var.environment}-${var.account_id}-logs"

  # ARNs are built from names (not read from resources) so policies are fully known at plan time.
  bucket_arns = { for k, v in local.buckets : k => "arn:aws:s3:::${v}" }
  logs_arn    = "arn:aws:s3:::${local.logs_bucket}"

  tags = merge(var.tags, {
    Project            = var.name_prefix
    Environment        = var.environment
    ManagedBy          = "terraform"
    DataClassification = "confidential"
  })
}

# ---------------------------------------------------------------------------- encryption key

data "aws_iam_policy_document" "key" {
  # Standard statement: delegates access control to IAM policies in this account. Without it the
  # key would be unmanageable. All other access is granted by the role policies in iam.tf.
  statement {
    sid       = "EnableIamPolicies"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${var.account_id}:root"]
    }
  }
}

resource "aws_kms_key" "lake" {
  description             = "${var.name_prefix} ${var.environment} data lake"
  enable_key_rotation     = true
  deletion_window_in_days = var.kms_deletion_window_days
  policy                  = data.aws_iam_policy_document.key.json
  tags                    = local.tags
}

resource "aws_kms_alias" "lake" {
  name          = "alias/${var.name_prefix}-${var.environment}-lake"
  target_key_id = aws_kms_key.lake.key_id
}

# ---------------------------------------------------------------------------- data buckets

resource "aws_s3_bucket" "data" {
  for_each = local.buckets

  bucket        = each.value
  force_destroy = false
  tags          = merge(local.tags, { Layer = each.key })
}

resource "aws_s3_bucket_public_access_block" "data" {
  for_each = aws_s3_bucket.data

  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "data" {
  for_each = aws_s3_bucket.data

  bucket = each.value.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "data" {
  for_each = aws_s3_bucket.data

  bucket = each.value.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  for_each = aws_s3_bucket.data

  bucket = each.value.id

  rule {
    bucket_key_enabled = true

    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.lake.arn
    }
  }
}

resource "aws_s3_bucket_logging" "data" {
  for_each = aws_s3_bucket.data

  bucket        = each.value.id
  target_bucket = aws_s3_bucket.logs.id
  target_prefix = "${each.key}/"

  # S3 validates the delivery permission when logging is enabled, so the log bucket's policy must exist first.
  depends_on = [aws_s3_bucket_policy.logs]
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  for_each = aws_s3_bucket.data

  bucket = each.value.id

  rule {
    id     = "abort-incomplete-uploads-and-expire-old-versions"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_expiry_days
    }
  }

  dynamic "rule" {
    for_each = each.key == "raw" ? [1] : []

    content {
      id     = "tier-raw-data"
      status = "Enabled"

      filter {}

      transition {
        days          = var.raw_ia_transition_days
        storage_class = "STANDARD_IA"
      }

      transition {
        days          = var.raw_glacier_transition_days
        storage_class = "GLACIER"
      }
    }
  }
}

data "aws_iam_policy_document" "data_bucket" {
  for_each = local.buckets

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [local.bucket_arns[each.key], "${local.bucket_arns[each.key]}/*"]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid       = "DenyUnencryptedUploads"
    effect    = "Deny"
    actions   = ["s3:PutObject"]
    resources = ["${local.bucket_arns[each.key]}/*"]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["aws:kms"]
    }
  }

  statement {
    sid       = "DenyOtherKmsKeys"
    effect    = "Deny"
    actions   = ["s3:PutObject"]
    resources = ["${local.bucket_arns[each.key]}/*"]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "StringNotEqualsIfExists"
      variable = "s3:x-amz-server-side-encryption-aws-kms-key-id"
      values   = [aws_kms_key.lake.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "data" {
  for_each = aws_s3_bucket.data

  bucket = each.value.id
  policy = data.aws_iam_policy_document.data_bucket[each.key].json

  depends_on = [aws_s3_bucket_public_access_block.data]
}

# ---------------------------------------------------------------------------- access-log bucket
# S3 server access logging cannot write to a bucket encrypted with a customer-managed KMS key, so
# this one bucket uses SSE-S3 (AES256). It holds access logs only, never data.

resource "aws_s3_bucket" "logs" {
  bucket        = local.logs_bucket
  force_destroy = false
  tags          = merge(local.tags, { Layer = "logs" })
}

resource "aws_s3_bucket_public_access_block" "logs" {
  bucket                  = aws_s3_bucket.logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "logs" {
  bucket = aws_s3_bucket.logs.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    id     = "expire-logs"
    status = "Enabled"

    filter {}

    expiration {
      days = var.log_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

data "aws_iam_policy_document" "logs_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [local.logs_arn, "${local.logs_arn}/*"]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid       = "AllowS3LogDelivery"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${local.logs_arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["logging.s3.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = [for arn in values(local.bucket_arns) : arn]
    }
  }
}

resource "aws_s3_bucket_policy" "logs" {
  bucket = aws_s3_bucket.logs.id
  policy = data.aws_iam_policy_document.logs_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.logs]
}

# ---------------------------------------------------------------------------- cost guard

resource "aws_budgets_budget" "monthly" {
  count = var.create_budget && length(var.budget_alert_emails) > 0 ? 1 : 0

  account_id   = var.account_id
  name         = "${var.name_prefix}-${var.environment}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.budget_alert_emails
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.budget_alert_emails
  }
}
