# Redshift Serverless namespace + workgroup with cost guardrails and a least-privilege COPY role.
# PLAN ONLY in this repository: the provider uses literal "mock" credentials and skips account lookups; nothing is applied.
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.82"
    }
  }
}

variable "base_capacity_rpu" {
  type    = number
  default = 8
}

variable "max_capacity_rpu" {
  type    = number
  default = 32
}

variable "monthly_rpu_hours_limit" {
  description = "Hard cap on compute; the usage limit action below stops queries beyond it"
  type        = number
  default     = 200
}

variable "publicly_accessible" {
  type    = bool
  default = false
}

variable "subnet_ids" {
  type    = list(string)
  default = ["subnet-00000001", "subnet-00000002", "subnet-00000003"]
}

variable "security_group_ids" {
  type    = list(string)
  default = ["sg-00000001"]
}

provider "aws" {
  region                      = "eu-west-1"
  access_key                  = "mock"
  secret_key                  = "mock"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
}

locals {
  account    = "123456789012"
  bucket     = "elt-landing-123456789012"
  bucket_arn = "arn:aws:s3:::elt-landing-123456789012" # constructed so the IAM policy is known at plan time
}

resource "aws_s3_bucket" "landing" {
  bucket = local.bucket
}

resource "aws_s3_bucket_public_access_block" "landing" {
  bucket                  = aws_s3_bucket.landing.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "landing" {
  bucket = aws_s3_bucket.landing.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_iam_role" "copy" {
  name = "redshift-copy-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "redshift-serverless.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = local.account } }
    }]
  })
}

resource "aws_iam_role_policy" "copy" {
  name = "read-landing"
  role = aws_iam_role.copy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = [local.bucket_arn] },
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${local.bucket_arn}/*"] },
    ]
  })
}

resource "aws_kms_key" "warehouse" {
  description         = "Redshift Serverless namespace key"
  enable_key_rotation = true
}

resource "aws_redshiftserverless_namespace" "elt" {
  namespace_name       = "elt"
  db_name              = "analytics"
  admin_username       = "admin"
  manage_admin_password = true
  kms_key_id           = aws_kms_key.warehouse.arn
  iam_roles            = [aws_iam_role.copy.arn]
  log_exports          = ["userlog", "connectionlog", "useractivitylog"]
}

resource "aws_redshiftserverless_workgroup" "elt" {
  namespace_name      = aws_redshiftserverless_namespace.elt.namespace_name
  workgroup_name      = "elt"
  base_capacity       = var.base_capacity_rpu
  max_capacity        = var.max_capacity_rpu
  publicly_accessible = var.publicly_accessible
  subnet_ids          = var.subnet_ids
  security_group_ids  = var.security_group_ids
  enhanced_vpc_routing = true
}

resource "aws_redshiftserverless_usage_limit" "compute" {
  resource_arn = aws_redshiftserverless_workgroup.elt.arn
  usage_type   = "serverless-compute"
  amount       = var.monthly_rpu_hours_limit
  period       = "monthly"
  breach_action = "deactivate"
}
