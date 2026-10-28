# Organisation guardrails. The provider below points at a LOCAL moto server (credentials are the literal "mock");
# nothing here contacts real AWS. To use against a real organisation you would remove the endpoints block and
# review every policy: that step is deliberately not provided by this repository.
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

provider "aws" {
  region                      = "eu-west-1"
  access_key                  = "mock"
  secret_key                  = "mock"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true

  endpoints {
    organizations = "http://127.0.0.1:${var.mock_port}"
    config        = "http://127.0.0.1:${var.mock_port}"
    sts           = "http://127.0.0.1:${var.mock_port}"
  }
}

locals {
  org           = jsondecode(file("${path.module}/../org.json"))
  policy_files  = fileset("${path.module}/../policies", "*.json")
  policy_names  = [for f in local.policy_files : trimsuffix(f, ".json")]
  ous           = local.org.root.ous
  # (target, policy) pairs. FullAWSAccess is NOT managed here: AWS attaches it to every new OU and account by default,
  # and detaching it (needed for allow-list OUs) is not expressible with this provider; see landing_zone_guardrails.tooling.reconcile_defaults.
  attachments = merge(
    { for p in local.org.root.policies : "root/${p}" => { target = "root", policy = p } if p != "FullAWSAccess" },
    merge([for ou, cfg in local.ous : { for p in cfg.policies : "${ou}/${p}" => { target = ou, policy = p } if p != "FullAWSAccess" }]...)
  )
}

data "aws_organizations_organization" "this" {}

resource "aws_organizations_organizational_unit" "ou" {
  for_each  = local.ous
  name      = each.key
  parent_id = data.aws_organizations_organization.this.roots[0].id
}

resource "aws_organizations_policy" "scp" {
  for_each = toset(local.policy_names)
  name     = each.key
  type     = "SERVICE_CONTROL_POLICY"
  content  = jsonencode(jsondecode(file("${path.module}/../policies/${each.key}.json")))
}

resource "aws_organizations_policy_attachment" "attach" {
  for_each  = local.attachments
  policy_id = aws_organizations_policy.scp[each.value.policy].id
  target_id = each.value.target == "root" ? data.aws_organizations_organization.this.roots[0].id : aws_organizations_organizational_unit.ou[each.value.target].id
}

resource "aws_config_configuration_recorder" "this" {
  name     = "default"
  role_arn = "arn:aws:iam::123456789012:role/config-recorder"
}

resource "aws_config_config_rule" "required" {
  for_each = toset(local.org.required_config_rules)
  name     = lower(replace(each.key, "_", "-"))

  source {
    owner             = "AWS"
    source_identifier = each.key
  }

  depends_on = [aws_config_configuration_recorder.this]
}

output "policy_ids" {
  value = { for k, p in aws_organizations_policy.scp : k => p.id }
}
