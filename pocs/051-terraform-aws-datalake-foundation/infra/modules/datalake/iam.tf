# Three roles, each limited to the one job it needs. Allow statements name exact actions and exact
# resources (no "*"). Explicit denies stop even a mis-granted role from deleting data, changing
# bucket policy or touching the key lifecycle.

locals {
  all_bucket_resources = concat(
    values(local.bucket_arns),
    [for arn in values(local.bucket_arns) : "${arn}/*"],
  )
  admin_deny_actions = [
    "s3:DeleteBucket",
    "s3:DeleteBucketPolicy",
    "s3:DeleteObject",
    "s3:DeleteObjectVersion",
    "s3:PutBucketPolicy",
    "s3:PutBucketPublicAccessBlock",
    "s3:PutEncryptionConfiguration",
    "s3:PutBucketVersioning",
  ]
  key_deny_actions = [
    "kms:ScheduleKeyDeletion",
    "kms:DisableKey",
    "kms:DisableKeyRotation",
    "kms:PutKeyPolicy",
  ]
  roles = {
    ingest    = var.ingest_principal_arns
    transform = var.transform_principal_arns
    analyst   = var.analyst_principal_arns
  }
}

data "aws_iam_policy_document" "assume" {
  for_each = local.roles

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = each.value
    }

    dynamic "condition" {
      for_each = each.key == "analyst" ? [1] : []

      content {
        test     = "Bool"
        variable = "aws:MultiFactorAuthPresent"
        values   = ["true"]
      }
    }
  }
}

resource "aws_iam_role" "lake" {
  for_each = local.roles

  name                 = "${var.name_prefix}-${var.environment}-${each.key}"
  assume_role_policy   = data.aws_iam_policy_document.assume[each.key].json
  max_session_duration = 3600
  tags                 = local.tags
}

data "aws_iam_policy_document" "role" {
  for_each = local.roles

  # ---- ingest: write raw only
  dynamic "statement" {
    for_each = each.key == "ingest" ? [1] : []

    content {
      sid       = "WriteRaw"
      actions   = ["s3:PutObject", "s3:AbortMultipartUpload"]
      resources = ["${local.bucket_arns["raw"]}/*"]
    }
  }

  dynamic "statement" {
    for_each = each.key == "ingest" ? [1] : []

    content {
      sid       = "ListRaw"
      actions   = ["s3:ListBucket"]
      resources = [local.bucket_arns["raw"]]
    }
  }

  dynamic "statement" {
    for_each = each.key == "ingest" ? [1] : []

    content {
      sid       = "EncryptWithLakeKey"
      actions   = ["kms:GenerateDataKey", "kms:Encrypt", "kms:DescribeKey"]
      resources = [aws_kms_key.lake.arn]
    }
  }

  # ---- transform: read raw, write curated
  dynamic "statement" {
    for_each = each.key == "transform" ? [1] : []

    content {
      sid       = "ReadRawAndCurated"
      actions   = ["s3:GetObject"]
      resources = ["${local.bucket_arns["raw"]}/*", "${local.bucket_arns["curated"]}/*"]
    }
  }

  dynamic "statement" {
    for_each = each.key == "transform" ? [1] : []

    content {
      sid       = "WriteCurated"
      actions   = ["s3:PutObject", "s3:AbortMultipartUpload"]
      resources = ["${local.bucket_arns["curated"]}/*"]
    }
  }

  dynamic "statement" {
    for_each = each.key == "transform" ? [1] : []

    content {
      sid       = "ListLakeBuckets"
      actions   = ["s3:ListBucket"]
      resources = [local.bucket_arns["raw"], local.bucket_arns["curated"]]
    }
  }

  dynamic "statement" {
    for_each = each.key == "transform" ? [1] : []

    content {
      sid       = "UseLakeKey"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:Encrypt", "kms:DescribeKey"]
      resources = [aws_kms_key.lake.arn]
    }
  }

  # ---- analyst: read curated only
  dynamic "statement" {
    for_each = each.key == "analyst" ? [1] : []

    content {
      sid       = "ReadCurated"
      actions   = ["s3:GetObject"]
      resources = ["${local.bucket_arns["curated"]}/*"]
    }
  }

  dynamic "statement" {
    for_each = each.key == "analyst" ? [1] : []

    content {
      sid       = "ListCurated"
      actions   = ["s3:ListBucket"]
      resources = [local.bucket_arns["curated"]]
    }
  }

  dynamic "statement" {
    for_each = each.key == "analyst" ? [1] : []

    content {
      sid       = "DecryptWithLakeKey"
      actions   = ["kms:Decrypt", "kms:DescribeKey"]
      resources = [aws_kms_key.lake.arn]
    }
  }

  # ---- all roles: guard rails
  statement {
    sid       = "DenyDestructiveS3"
    effect    = "Deny"
    actions   = local.admin_deny_actions
    resources = local.all_bucket_resources
  }

  statement {
    sid       = "DenyKeyLifecycle"
    effect    = "Deny"
    actions   = local.key_deny_actions
    resources = [aws_kms_key.lake.arn]
  }
}

resource "aws_iam_role_policy" "lake" {
  for_each = local.roles

  name   = "least-privilege"
  role   = aws_iam_role.lake[each.key].id
  policy = data.aws_iam_policy_document.role[each.key].json
}
