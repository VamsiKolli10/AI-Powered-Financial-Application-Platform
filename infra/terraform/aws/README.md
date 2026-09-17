# AWS infrastructure

This Terraform root defines a production-shaped staging environment for the platform:

- a multi-AZ VPC with public, private, database, and cache subnets;
- a private-endpoint EKS cluster with an encrypted managed node group;
- private RDS PostgreSQL and TLS-enabled ElastiCache Redis;
- private Amazon MSK with TLS, SASL/SCRAM authentication, and CloudWatch broker logs;
- immutable, scan-on-push ECR repositories for the backend and dashboard images; and
- generated application credentials stored in AWS Secrets Manager.

It is infrastructure as code, not evidence of a currently running public environment. Applying it
creates billable AWS resources. Review the plan and the AWS pricing pages before proceeding.

## Prerequisites

- Terraform 1.10 or newer
- AWS credentials with permission to manage the resources above
- an S3 state bucket and DynamoDB-compatible locking configuration for shared environments
- AWS CLI, Docker, `jq`, `kubectl`, and `kustomize` for application deployment

## Initialize and plan

The backend block is intentionally empty so each account supplies its own state location:

```bash
cd infra/terraform/aws
cp terraform.tfvars.example terraform.tfvars

terraform init \
  -backend-config="bucket=YOUR_TERRAFORM_STATE_BUCKET" \
  -backend-config="key=financial-ai/staging.tfstate" \
  -backend-config="region=us-east-1" \
  -backend-config="use_lockfile=true" \
  -backend-config="encrypt=true"

terraform fmt -check -recursive
terraform validate
terraform plan -out=staging.tfplan
terraform apply staging.tfplan
```

Use `TF_VAR_openai_api_key` for the optional OpenAI key. Do not put it in `terraform.tfvars` or
commit it. Terraform state contains generated credentials even though outputs are marked sensitive,
so the state bucket must be private, encrypted, versioned, and access-controlled.

## Deploy the application

After `terraform apply`, run the repository script from a machine that can reach the private EKS
API endpoint:

```bash
./scripts/deploy-aws.sh
```

The script builds immutable images, pushes them to ECR, reads the application secret without
printing it, creates the Kubernetes Secret, injects the MSK endpoint, applies the AWS Kustomize
overlay, runs Alembic, and waits for the gateway and dashboard rollouts.

The AWS overlay removes the local PostgreSQL, Redis, and Kafka workloads. Application pods use RDS,
ElastiCache, and MSK instead. MSK traffic uses `SASL_SSL` with SCRAM-SHA-512; Redis uses TLS.

### Rollback

Images are tagged with the source commit and ECR tags are immutable. To roll back an application
release, set `IMAGE_TAG` to a previously healthy commit and rerun the deployment script:

```bash
SKIP_BUILD=true IMAGE_TAG=PREVIOUS_COMMIT_SHA ./scripts/deploy-aws.sh
```

Kubernetes performs a rolling update and readiness probes keep unavailable replacements out of
service. For an in-progress rollout, inspect it with `kubectl -n financial-ai rollout status` and
use `kubectl -n financial-ai rollout undo deployment/NAME` when immediate reversal is required.

## Production adjustments

The checked-in defaults are cost-conscious staging values. Before treating the environment as
production, use one NAT gateway per AZ, enable Multi-AZ RDS and deletion protection, review node and
broker sizing, put the ingress behind TLS and DNS, restrict operator access, and add monitoring and
on-call alerting. The repository does not claim a live AWS deployment until an environment is
actually provisioned and exercised.
