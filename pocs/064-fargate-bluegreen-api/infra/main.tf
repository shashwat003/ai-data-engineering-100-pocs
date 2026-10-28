# ECS Fargate service behind an ALB with two target groups (blue/green via listener weights), a GitHub OIDC deploy role and
# task roles. This configuration is only PLANNED by the POC (never applied, and nothing contacts AWS): the provider block uses
# literal "mock" credentials and skips every account lookup.
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.82"
    }
  }
}

variable "repository" {
  description = "GitHub repository allowed to deploy, as owner/name"
  type        = string
  default     = "example-org/inference-api"
}

variable "image" {
  description = "Container image, pinned by digest"
  type        = string
  default     = "123456789012.dkr.ecr.eu-west-1.amazonaws.com/inference-api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}

variable "deploy_subject" {
  description = "Override of the OIDC subject claim the deploy role trusts (used by the POC to plan deliberately unsafe variants)"
  type        = string
  default     = ""
}

variable "certificate_arn" {
  type    = string
  default = "arn:aws:acm:eu-west-1:123456789012:certificate/00000000-0000-0000-0000-000000000000"
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
  account_id = "123456789012"
  oidc_arn   = "arn:aws:iam::${local.account_id}:oidc-provider/token.actions.githubusercontent.com"
  # constructed (not resource-attribute) ARNs keep the permission policy fully known at plan time, so it can be linted before apply
  task_execution_role_arn = "arn:aws:iam::${local.account_id}:role/inference-task-execution"
  task_role_arn           = "arn:aws:iam::${local.account_id}:role/inference-task"
  subject                 = var.deploy_subject != "" ? var.deploy_subject : "repo:${var.repository}:environment:production"
}

resource "aws_vpc" "v" {
  cidr_block = "10.0.0.0/16"
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.v.id
  cidr_block        = "10.0.${count.index + 1}.0/24"
  availability_zone = ["eu-west-1a", "eu-west-1b"][count.index]
}

resource "aws_security_group" "lb" {
  name   = "inference-lb"
  vpc_id = aws_vpc.v.id
}

resource "aws_security_group" "svc" {
  name   = "inference-svc"
  vpc_id = aws_vpc.v.id
}

resource "aws_lb" "lb" {
  name                       = "inference"
  load_balancer_type         = "application"
  internal                   = true
  subnets                    = aws_subnet.private[*].id
  security_groups            = [aws_security_group.lb.id]
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "blue" {
  name        = "inference-blue"
  port        = 8080
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.v.id
  health_check {
    path = "/healthz"
  }
  deregistration_delay = 30
}

resource "aws_lb_target_group" "green" {
  name        = "inference-green"
  port        = 8080
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.v.id
  health_check {
    path = "/healthz"
  }
  deregistration_delay = 30
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.lb.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.lb.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn
  default_action {
    type = "forward"
    forward {
      target_group {
        arn    = aws_lb_target_group.blue.arn
        weight = 100
      }
      target_group {
        arn    = aws_lb_target_group.green.arn
        weight = 0
      }
    }
  }
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/inference"
  retention_in_days = 30
}

data "aws_iam_policy_document" "tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task_execution" {
  name               = "inference-task-execution"
  assume_role_policy = data.aws_iam_policy_document.tasks_assume.json
}

resource "aws_iam_role" "task" {
  name               = "inference-task"
  assume_role_policy = data.aws_iam_policy_document.tasks_assume.json
}

resource "aws_ecs_cluster" "c" {
  name = "inference"
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_task_definition" "t" {
  family                   = "inference"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = local.task_execution_role_arn
  task_role_arn            = local.task_role_arn
  container_definitions = jsonencode([{
    name                   = "app"
    image                  = var.image
    essential              = true
    readonlyRootFilesystem = true
    portMappings           = [{ containerPort = 8080 }]
    logConfiguration = {
      logDriver = "awslogs"
      options   = { "awslogs-group" = "/ecs/inference", "awslogs-region" = "eu-west-1", "awslogs-stream-prefix" = "app" }
    }
  }])
}

resource "aws_ecs_service" "s" {
  name            = "inference"
  cluster         = aws_ecs_cluster.c.id
  task_definition = aws_ecs_task_definition.t.arn
  desired_count   = 2
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.svc.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.blue.arn
    container_name   = "app"
    container_port   = 8080
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
}

# --- GitHub Actions OIDC: the deploy role is assumable only by one repository's production environment -------------
data "aws_iam_policy_document" "deploy_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.subject]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = "inference-github-deploy"
  assume_role_policy   = data.aws_iam_policy_document.deploy_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "deploy_permissions" {
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    actions   = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage"]
    resources = ["arn:aws:ecr:eu-west-1:${local.account_id}:repository/inference-api"]
  }
  statement {
    actions   = ["ecs:RegisterTaskDefinition", "ecs:DescribeTaskDefinition"]
    resources = ["*"]
  }
  statement {
    actions   = ["ecs:UpdateService", "ecs:DescribeServices"]
    resources = ["arn:aws:ecs:eu-west-1:${local.account_id}:service/inference/inference"]
  }
  statement {
    actions   = ["elasticloadbalancing:ModifyListener", "elasticloadbalancing:DescribeListeners", "elasticloadbalancing:DescribeTargetHealth"]
    resources = ["*"]
  }
  statement {
    actions   = ["iam:PassRole"]
    resources = [local.task_execution_role_arn, local.task_role_arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy_permissions.json
}
