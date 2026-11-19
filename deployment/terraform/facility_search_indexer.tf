#
# Facilities search indexer (/map on OpenSearch).
#
# Runs `manage.py opensearch_facility_indexer`, which applies
# api_facilitysearchoutbox entries to the `facilities` OpenSearch index.
# It is the CLI task definition (same image, environment, secrets and task
# role, which already has access to the OpenSearch domain) with the
# command fixed. Leave the desired count at 0 until the first
# `rebuild_facilities_search_index` has been run in the environment; the
# indexer drops outbox entries while no index exists.
#
locals {
  facility_search_indexer_container = merge(
    jsondecode(data.template_file.app_cli.rendered)[0],
    {
      command = ["opensearch_facility_indexer"]
    }
  )
}

resource "aws_ecs_task_definition" "facility_search_indexer" {
  family                   = "${local.short}FacilitySearchIndexer"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.facility_search_indexer_fargate_cpu
  memory                   = var.facility_search_indexer_fargate_memory

  task_role_arn      = aws_iam_role.app_task_role.arn
  execution_role_arn = aws_iam_role.ecs_task_execution_role.arn

  container_definitions = jsonencode([local.facility_search_indexer_container])
}

resource "aws_ecs_service" "facility_search_indexer" {
  name            = "${local.short}FacilitySearchIndexer"
  cluster         = aws_ecs_cluster.app.id
  task_definition = aws_ecs_task_definition.facility_search_indexer.arn

  # One indexer is enough: claims use FOR UPDATE SKIP LOCKED, so a second
  # one during a deploy is safe but not needed.
  desired_count                      = var.facility_search_indexer_desired_count
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  launch_type = "FARGATE"

  network_configuration {
    security_groups = [aws_security_group.app.id]
    subnets         = module.vpc.private_subnet_ids
  }
}

variable "facility_search_indexer_desired_count" {
  type    = number
  default = 0
}

variable "facility_search_indexer_fargate_cpu" {
  type    = string
  default = "256"
}

variable "facility_search_indexer_fargate_memory" {
  type    = string
  default = "512"
}
