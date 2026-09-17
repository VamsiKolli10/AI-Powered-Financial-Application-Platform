# Kubernetes deployment

This Kustomize base runs the complete platform in a local `kind` cluster: the React dashboard,
five FastAPI services, PostgreSQL, Redis, Kafka, database migrations, Services, health probes,
resource bounds, and HPAs. It intentionally disables provider calls (`LLM_ENABLED=false`) so the
deterministic fallback path works without placing an API key in the cluster.

## Local `kind` run

Prerequisites: Docker, `kind`, and `kubectl`.

```bash
kind create cluster --name financial-ai

docker build -f infra/docker/Dockerfile -t financial-ai-platform/backend:local .
docker build -f web/Dockerfile -t financial-ai-platform/dashboard:local web

kind load docker-image financial-ai-platform/backend:local --name financial-ai
kind load docker-image financial-ai-platform/dashboard:local --name financial-ai

kubectl apply -k infra/k8s
kubectl -n financial-ai wait --for=condition=complete job/database-migrations --timeout=180s
kubectl -n financial-ai rollout status deployment/gateway --timeout=180s
kubectl -n financial-ai rollout status deployment/dashboard --timeout=180s
kubectl -n financial-ai port-forward service/dashboard 3000:80
```

Open <http://localhost:3000>. Seed the database from the completed migration pod if you want
the included `demo@example.com` / `demo-password` login:

```bash
kubectl -n financial-ai exec job/database-migrations -- python -m scripts.seed
```

For ingress, install the ingress-nginx controller for `kind`, map `ledger-ai.local` to localhost,
and open `http://ledger-ai.local`. The checked-in Ingress documents the route but port-forwarding
keeps the default setup controller-independent.

## Production boundary

The generated secrets and in-cluster data services are for local development only. A production
overlay should use managed PostgreSQL/Redis/Kafka, External Secrets backed by AWS Secrets Manager,
immutable registry tags, network policies, PodDisruptionBudgets, persistent volumes, cert-manager,
and a real DNS name. EKS and Terraform are not represented as complete until those resources have
been provisioned and exercised.
