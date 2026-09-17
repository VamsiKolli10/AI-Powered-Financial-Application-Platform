output "aws_region" {
  value = var.aws_region
}

output "cluster_name" {
  value = module.eks.cluster_name
}

output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "ecr_repository_urls" {
  value = { for name, repository in aws_ecr_repository.service : name => repository.repository_url }
}

output "database_address" {
  value     = aws_db_instance.platform.address
  sensitive = true
}

output "redis_primary_endpoint" {
  value     = aws_elasticache_replication_group.platform.primary_endpoint_address
  sensitive = true
}

output "kafka_bootstrap_brokers_sasl_scram" {
  value     = aws_msk_cluster.platform.bootstrap_brokers_sasl_scram
  sensitive = true
}

output "application_secret_arn" {
  value = aws_secretsmanager_secret.platform.arn
}
