# DELIBERATELY INSECURE. Exists only so the policy checker has real Terraform output to fail on.
# Never apply. Public bucket, no encryption, no versioning, wildcard IAM, no key rotation.
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.82"
    }
  }
}

provider "aws" {
  region                      = "eu-west-1"
  access_key                  = "mock"
  secret_key                  = "mock"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
}

resource "aws_s3_bucket" "bad" {
  bucket = "insecure-example-bucket"
}

resource "aws_s3_bucket_public_access_block" "bad" {
  bucket                  = aws_s3_bucket.bad.id
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_server_side_encryption_configuration" "bad" {
  bucket = aws_s3_bucket.bad.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_kms_key" "bad" {
  description         = "no rotation"
  enable_key_rotation = false
}

resource "aws_iam_role" "bad" {
  name               = "insecure-admin"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { AWS = "*" } }] })
}

resource "aws_iam_role_policy" "bad" {
  name = "everything"
  role = aws_iam_role.bad.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "*", Resource = "*" }]
  })
}
