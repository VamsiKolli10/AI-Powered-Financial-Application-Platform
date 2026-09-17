resource "random_password" "database" {
  length  = 32
  special = false
}

resource "random_password" "redis" {
  length  = 32
  special = false
}

resource "random_password" "kafka" {
  length  = 32
  special = false
}

resource "random_password" "jwt" {
  length  = 48
  special = false
}

resource "random_password" "internal_service" {
  length  = 48
  special = false
}

resource "aws_security_group" "database" {
  name_prefix = "${local.name}-postgres-"
  description = "PostgreSQL access from EKS application nodes"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "PostgreSQL from EKS nodes"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle { create_before_destroy = true }
}

resource "aws_db_subnet_group" "platform" {
  name       = local.name
  subnet_ids = module.vpc.database_subnets
}

resource "aws_db_instance" "platform" {
  identifier = local.name

  engine         = "postgres"
  engine_version = "16"
  instance_class = var.database_instance_class

  db_name  = "finai"
  username = "finai"
  password = random_password.database.result
  port     = 5432

  allocated_storage     = 20
  max_allocated_storage = 100
  storage_type          = "gp3"
  storage_encrypted     = true
  multi_az              = var.database_multi_az

  db_subnet_group_name   = aws_db_subnet_group.platform.name
  vpc_security_group_ids = [aws_security_group.database.id]
  publicly_accessible    = false

  backup_retention_period    = 7
  auto_minor_version_upgrade = true
  deletion_protection        = var.database_deletion_protection
  skip_final_snapshot        = !var.database_deletion_protection
  final_snapshot_identifier  = var.database_deletion_protection ? "${local.name}-final" : null

  performance_insights_enabled    = true
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
}

resource "aws_security_group" "redis" {
  name_prefix = "${local.name}-redis-"
  description = "Redis access from EKS application nodes"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "Redis TLS from EKS nodes"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle { create_before_destroy = true }
}

resource "aws_elasticache_subnet_group" "platform" {
  name       = local.name
  subnet_ids = module.vpc.elasticache_subnets
}

resource "aws_elasticache_replication_group" "platform" {
  replication_group_id = local.name
  description          = "Redis for ${local.name}"

  engine         = "redis"
  engine_version = "7.1"
  node_type      = var.redis_node_type
  port           = 6379

  num_cache_clusters         = 2
  automatic_failover_enabled = true
  multi_az_enabled           = true

  subnet_group_name  = aws_elasticache_subnet_group.platform.name
  security_group_ids = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = random_password.redis.result

  snapshot_retention_limit = 3
  apply_immediately        = false
}

resource "aws_kms_key" "kafka" {
  description             = "MSK data and SCRAM secret encryption for ${local.name}"
  deletion_window_in_days = 14
  enable_key_rotation     = true
}

resource "aws_kms_alias" "kafka" {
  name          = "alias/${local.name}-kafka"
  target_key_id = aws_kms_key.kafka.key_id
}

resource "aws_security_group" "kafka" {
  name_prefix = "${local.name}-kafka-"
  description = "Kafka SASL TLS access from EKS application nodes"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "MSK SASL SCRAM TLS from EKS nodes"
    from_port       = 9096
    to_port         = 9096
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle { create_before_destroy = true }
}

resource "aws_cloudwatch_log_group" "kafka" {
  name              = "/aws/msk/${local.name}"
  retention_in_days = 30
}

resource "aws_secretsmanager_secret" "kafka_scram" {
  name       = "AmazonMSK_${replace(local.name, "-", "_")}_scram"
  kms_key_id = aws_kms_key.kafka.arn
}

resource "aws_secretsmanager_secret_version" "kafka_scram" {
  secret_id = aws_secretsmanager_secret.kafka_scram.id
  secret_string = jsonencode({
    username = "finai"
    password = random_password.kafka.result
  })
}

resource "aws_msk_cluster" "platform" {
  cluster_name           = local.name
  kafka_version          = "3.9.x"
  number_of_broker_nodes = var.availability_zone_count

  broker_node_group_info {
    instance_type   = var.kafka_instance_type
    client_subnets  = module.vpc.private_subnets
    security_groups = [aws_security_group.kafka.id]

    storage_info {
      ebs_storage_info { volume_size = 50 }
    }
  }

  client_authentication {
    sasl { scram = true }
    unauthenticated = false
  }

  encryption_info {
    encryption_at_rest_kms_key_arn = aws_kms_key.kafka.arn
    encryption_in_transit {
      client_broker = "TLS"
      in_cluster    = true
    }
  }

  logging_info {
    broker_logs {
      cloudwatch_logs {
        enabled   = true
        log_group = aws_cloudwatch_log_group.kafka.name
      }
    }
  }
}

resource "aws_msk_scram_secret_association" "platform" {
  cluster_arn     = aws_msk_cluster.platform.arn
  secret_arn_list = [aws_secretsmanager_secret.kafka_scram.arn]

  depends_on = [aws_secretsmanager_secret_version.kafka_scram]
}

resource "aws_secretsmanager_secret" "platform" {
  name                    = "${local.name}/application"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "platform" {
  secret_id = aws_secretsmanager_secret.platform.id
  secret_string = jsonencode({
    POSTGRES_USER          = "finai"
    POSTGRES_PASSWORD      = random_password.database.result
    POSTGRES_DB            = "finai"
    POSTGRES_HOST          = aws_db_instance.platform.address
    DATABASE_URL           = "postgresql+asyncpg://finai:${random_password.database.result}@${aws_db_instance.platform.address}:5432/finai"
    REDIS_URL              = "rediss://:${random_password.redis.result}@${aws_elasticache_replication_group.platform.primary_endpoint_address}:6379/0"
    KAFKA_SASL_USERNAME    = "finai"
    KAFKA_SASL_PASSWORD    = random_password.kafka.result
    JWT_SECRET             = random_password.jwt.result
    INTERNAL_SERVICE_TOKEN = random_password.internal_service.result
    OPENAI_API_KEY         = var.openai_api_key
  })
}
