mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = {
      names    = ["us-east-1a", "us-east-1b", "us-east-1c"]
      zone_ids = ["use1-az1", "use1-az2", "use1-az3"]
    }
  }

  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
      arn        = "arn:aws:iam::123456789012:user/terraform-test"
      user_id    = "AIDATEST"
    }
  }

  mock_data "aws_partition" {
    defaults = {
      partition          = "aws"
      dns_suffix         = "amazonaws.com"
      reverse_dns_prefix = "com.amazonaws"
    }
  }

  mock_data "aws_iam_session_context" {
    defaults = {
      issuer_arn = "arn:aws:iam::123456789012:user/terraform-test"
    }
  }

  mock_data "aws_iam_policy_document" {
    defaults = {
      json          = "{}"
      minified_json = "{}"
    }
  }
}
mock_provider "random" {}

run "staging_plan" {
  command = plan

  variables {
    project_name            = "financial-ai"
    environment             = "staging"
    availability_zone_count = 3
  }

  assert {
    condition     = length(aws_ecr_repository.service) == 2
    error_message = "The deployment requires separate backend and dashboard ECR repositories."
  }

  assert {
    condition     = aws_db_instance.platform.publicly_accessible == false
    error_message = "RDS must remain private."
  }

  assert {
    condition     = aws_elasticache_replication_group.platform.transit_encryption_enabled
    error_message = "Redis traffic must be encrypted in transit."
  }

  assert {
    condition     = aws_msk_cluster.platform.encryption_info[0].encryption_in_transit[0].client_broker == "TLS"
    error_message = "MSK clients must use TLS."
  }
}
