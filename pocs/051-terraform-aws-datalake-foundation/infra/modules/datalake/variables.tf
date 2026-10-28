variable "name_prefix" {
  description = "Short prefix for all resource names, e.g. \"acme-data\"."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.name_prefix))
    error_message = "name_prefix must be 3-21 chars: lowercase letters, digits, hyphens, starting with a letter."
  }
}

variable "environment" {
  description = "Deployment environment."
  type        = string

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "account_id" {
  description = "12-digit AWS account id. Passed in (not looked up) so plans need no AWS API calls."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be exactly 12 digits."
  }
}

variable "region" {
  description = "AWS region, used to scope the KMS ViaService condition."
  type        = string
  default     = "eu-west-1"
}

variable "ingest_principal_arns" {
  description = "IAM principals allowed to assume the ingest role (write raw data)."
  type        = list(string)

  validation {
    condition     = length(var.ingest_principal_arns) > 0 && alltrue([for a in var.ingest_principal_arns : can(regex("^arn:aws:iam::[0-9]{12}:", a))])
    error_message = "Provide at least one IAM principal ARN (arn:aws:iam::<account>:...)."
  }
}

variable "transform_principal_arns" {
  description = "IAM principals allowed to assume the transform role (read raw, write curated)."
  type        = list(string)

  validation {
    condition     = length(var.transform_principal_arns) > 0 && alltrue([for a in var.transform_principal_arns : can(regex("^arn:aws:iam::[0-9]{12}:", a))])
    error_message = "Provide at least one IAM principal ARN."
  }
}

variable "analyst_principal_arns" {
  description = "IAM principals allowed to assume the analyst role (read curated). MFA is required."
  type        = list(string)

  validation {
    condition     = length(var.analyst_principal_arns) > 0 && alltrue([for a in var.analyst_principal_arns : can(regex("^arn:aws:iam::[0-9]{12}:", a))])
    error_message = "Provide at least one IAM principal ARN."
  }
}

variable "kms_deletion_window_days" {
  description = "Waiting period before a scheduled key deletion takes effect."
  type        = number
  default     = 30

  validation {
    condition     = var.kms_deletion_window_days >= 7 && var.kms_deletion_window_days <= 30
    error_message = "kms_deletion_window_days must be between 7 and 30."
  }
}

variable "raw_ia_transition_days" {
  type    = number
  default = 30
}

variable "raw_glacier_transition_days" {
  type    = number
  default = 90

  validation {
    condition     = var.raw_glacier_transition_days > 30
    error_message = "S3 requires at least 30 days between the IA and Glacier transitions."
  }
}

variable "noncurrent_expiry_days" {
  description = "Days after which noncurrent object versions are deleted."
  type        = number
  default     = 90
}

variable "log_retention_days" {
  description = "Days to keep S3 server access logs."
  type        = number
  default     = 365
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget for the account; alerts at 80% forecast and 100% actual."
  type        = number
  default     = 25

  validation {
    condition     = var.monthly_budget_usd > 0
    error_message = "monthly_budget_usd must be positive."
  }
}

variable "budget_alert_emails" {
  description = "Recipients of budget alerts. If empty, no budget is created."
  type        = list(string)
  default     = []
}

variable "create_budget" {
  description = "Create the cost budget. Leave true; false exists only for mock environments whose API lacks Budgets support."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Extra tags applied to every resource."
  type        = map(string)
  default     = {}
}
