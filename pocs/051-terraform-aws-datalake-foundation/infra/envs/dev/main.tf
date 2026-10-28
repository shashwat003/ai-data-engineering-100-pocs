# Real-AWS entry point. NOTHING in this repository applies it; `make plan-dev` is not provided on
# purpose. If you apply it yourself, expect a small cost (one KMS key is about USD 1 per month).
provider "aws" {
  region = var.region

  default_tags {
    tags = { Repository = "100-real-world-ai-data-engineering-pocs" }
  }
}

variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "account_id" {
  type = string
}

variable "ingest_principal_arns" {
  type = list(string)
}

variable "transform_principal_arns" {
  type = list(string)
}

variable "analyst_principal_arns" {
  type = list(string)
}

variable "budget_alert_emails" {
  type    = list(string)
  default = []
}

module "datalake" {
  source = "../../modules/datalake"

  name_prefix              = "demo-data"
  environment              = "dev"
  account_id               = var.account_id
  region                   = var.region
  ingest_principal_arns    = var.ingest_principal_arns
  transform_principal_arns = var.transform_principal_arns
  analyst_principal_arns   = var.analyst_principal_arns
  budget_alert_emails      = var.budget_alert_emails
}

output "lake" {
  value = module.datalake.bucket_names
}
