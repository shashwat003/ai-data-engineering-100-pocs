# Catalog and query-engine configuration for the Iceberg lake. The provider points at a LOCAL moto server with literal
# "mock" credentials: nothing here contacts real AWS.
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.82"
    }
  }
}

variable "mock_port" {
  type    = number
  default = 5099
}

variable "create_workgroup" {
  description = "moto cannot list tags for Athena workgroups, so mock applies set this to false; plans keep the workgroup and are inspected."
  type        = bool
  default     = true
}

provider "aws" {
  region                      = "eu-west-1"
  access_key                  = "mock"
  secret_key                  = "mock"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  s3_use_path_style           = true

  endpoints {
    s3     = "http://127.0.0.1:${var.mock_port}"
    glue   = "http://127.0.0.1:${var.mock_port}"
    athena = "http://127.0.0.1:${var.mock_port}"
    sts    = "http://127.0.0.1:${var.mock_port}"
  }
}

locals {
  lake_bucket    = "demo-iceberg-lake"
  results_bucket = "demo-athena-results"
}

resource "aws_s3_bucket" "lake" {
  bucket = local.lake_bucket
}

resource "aws_s3_bucket" "results" {
  bucket = local.results_bucket
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "results" {
  bucket = aws_s3_bucket.results.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_glue_catalog_database" "lake" {
  name = "lake"
}

resource "aws_glue_catalog_table" "orders" {
  database_name = aws_glue_catalog_database.lake.name
  name          = "orders"
  table_type    = "EXTERNAL_TABLE"

  open_table_format_input {
    iceberg_input {
      metadata_operation = "CREATE"
      version            = "2"
    }
  }

  storage_descriptor {
    location = "s3://${local.lake_bucket}/warehouse/lake/orders"
    columns {
      name = "order_id"
      type = "bigint"
    }
    columns {
      name = "customer_id"
      type = "bigint"
    }
    columns {
      name = "ts"
      type = "timestamp"
    }
    columns {
      name = "amount"
      type = "double"
    }
  }
}

resource "aws_athena_workgroup" "analytics" {
  count = var.create_workgroup ? 1 : 0
  name  = "analytics"

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = 10737418240

    result_configuration {
      output_location = "s3://${local.results_bucket}/athena/"
      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }

    engine_version {
      selected_engine_version = "Athena engine version 3"
    }
  }
}
