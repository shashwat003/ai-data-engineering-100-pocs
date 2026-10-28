# Mock environment: every AWS call goes to a local moto server (or nowhere, for `plan`). The
# credentials are the literal strings "mock"; no real AWS account is ever contacted.
variable "mock_port" {
  type    = number
  default = 5099
}

variable "apply_budget" {
  description = "moto cannot complete aws_budgets_budget (describe_subscribers_for_notification is not implemented), so apply runs set this to false; plans keep the budget."
  type        = bool
  default     = true
}

provider "aws" {
  region                      = "eu-west-1"
  access_key                  = "mock"
  secret_key                  = "mock"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
  s3_use_path_style           = true

  endpoints {
    s3      = "http://127.0.0.1:${var.mock_port}"
    iam     = "http://127.0.0.1:${var.mock_port}"
    kms     = "http://127.0.0.1:${var.mock_port}"
    sts     = "http://127.0.0.1:${var.mock_port}"
    budgets = "http://127.0.0.1:${var.mock_port}"
  }
}

module "datalake" {
  source = "../../modules/datalake"

  name_prefix              = "mock-data"
  environment              = "dev"
  account_id               = "123456789012"
  ingest_principal_arns    = ["arn:aws:iam::123456789012:role/mock-ingest-job"]
  transform_principal_arns = ["arn:aws:iam::123456789012:role/mock-transform-job"]
  analyst_principal_arns   = ["arn:aws:iam::123456789012:user/mock-analyst"]
  budget_alert_emails      = ["alerts@example.com"]
  create_budget            = var.apply_budget
}

output "lake" {
  value = module.datalake.bucket_names
}
