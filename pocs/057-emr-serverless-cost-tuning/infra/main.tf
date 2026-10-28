# EMR Serverless application for Spark. PLAN ONLY: the provider uses literal "mock" credentials and skips account lookups,
# and nothing in this repository applies it.
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.82"
    }
  }
}

variable "architecture" {
  type    = string
  default = "ARM64"
}

variable "idle_timeout_minutes" {
  type    = number
  default = 10
}

variable "max_vcpu" {
  type    = number
  default = 64
}

variable "preinit_executors" {
  description = "Pre-initialised executor workers kept warm (billed while idle)"
  type        = number
  default     = 0
}

provider "aws" {
  region                      = "eu-west-1"
  access_key                  = "mock"
  secret_key                  = "mock"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
}

resource "aws_emrserverless_application" "spark" {
  name          = "orders-etl"
  release_label = "emr-7.5.0"
  type          = "spark"
  architecture  = var.architecture

  auto_start_configuration {
    enabled = true
  }

  auto_stop_configuration {
    enabled              = true
    idle_timeout_minutes = var.idle_timeout_minutes
  }

  maximum_capacity {
    cpu    = "${var.max_vcpu} vCPU"
    memory = "${var.max_vcpu * 4} GB"
    disk   = "${var.max_vcpu * 20} GB"
  }

  dynamic "initial_capacity" {
    for_each = var.preinit_executors > 0 ? [1] : []
    content {
      initial_capacity_type = "Executor"
      initial_capacity_config {
        worker_count = var.preinit_executors
        worker_configuration {
          cpu    = "4 vCPU"
          memory = "16 GB"
          disk   = "40 GB"
        }
      }
    }
  }

  tags = {
    owner       = "data-platform"
    cost-centre = "analytics"
  }
}
