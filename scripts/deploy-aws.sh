#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="${ROOT_DIR}/infra/terraform/aws"
NAMESPACE="financial-ai"
IMAGE_TAG="${IMAGE_TAG:-$(git -C "${ROOT_DIR}" rev-parse --short=12 HEAD)}"
SKIP_BUILD="${SKIP_BUILD:-false}"

for command in aws docker git jq kubectl kustomize terraform; do
  command -v "${command}" >/dev/null || {
    echo "Missing required command: ${command}" >&2
    exit 1
  }
done

AWS_REGION="$(terraform -chdir="${TF_DIR}" output -raw aws_region)"
CLUSTER_NAME="$(terraform -chdir="${TF_DIR}" output -raw cluster_name)"
SECRET_ARN="$(terraform -chdir="${TF_DIR}" output -raw application_secret_arn)"
KAFKA_BOOTSTRAP_SERVERS="$(terraform -chdir="${TF_DIR}" output -raw kafka_bootstrap_brokers_sasl_scram)"
BACKEND_REPOSITORY="$(terraform -chdir="${TF_DIR}" output -json ecr_repository_urls | jq -r .backend)"
DASHBOARD_REPOSITORY="$(terraform -chdir="${TF_DIR}" output -json ecr_repository_urls | jq -r .dashboard)"
REGISTRY="${BACKEND_REPOSITORY%%/*}"

aws eks update-kubeconfig --region "${AWS_REGION}" --name "${CLUSTER_NAME}"
if [[ "${SKIP_BUILD}" != "true" ]]; then
  aws ecr get-login-password --region "${AWS_REGION}" |
    docker login --username AWS --password-stdin "${REGISTRY}"

  docker build -f "${ROOT_DIR}/infra/docker/Dockerfile" \
    -t "${BACKEND_REPOSITORY}:${IMAGE_TAG}" "${ROOT_DIR}"
  docker push "${BACKEND_REPOSITORY}:${IMAGE_TAG}"

  docker build -f "${ROOT_DIR}/web/Dockerfile" \
    -t "${DASHBOARD_REPOSITORY}:${IMAGE_TAG}" "${ROOT_DIR}/web"
  docker push "${DASHBOARD_REPOSITORY}:${IMAGE_TAG}"
fi

kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

aws secretsmanager get-secret-value \
  --region "${AWS_REGION}" \
  --secret-id "${SECRET_ARN}" \
  --query SecretString \
  --output text |
  jq --arg namespace "${NAMESPACE}" '{
    apiVersion: "v1",
    kind: "Secret",
    metadata: {name: "platform-secrets", namespace: $namespace},
    type: "Opaque",
    data: with_entries(.value |= @base64)
  }' |
  kubectl apply -f -

kubectl -n "${NAMESPACE}" create configmap platform-aws-config \
  --from-literal="KAFKA_BOOTSTRAP_SERVERS=${KAFKA_BOOTSTRAP_SERVERS}" \
  --dry-run=client -o yaml |
  kubectl apply -f -

RENDER_DIR="$(mktemp -d)"
trap 'rm -r -- "${RENDER_DIR}"' EXIT
cp -R "${ROOT_DIR}/infra/k8s" "${RENDER_DIR}/k8s"
cp -R "${ROOT_DIR}/infra/k8s-aws" "${RENDER_DIR}/k8s-aws"

pushd "${RENDER_DIR}/k8s-aws" >/dev/null
kustomize edit set image \
  "financial-ai-platform/backend=${BACKEND_REPOSITORY}:${IMAGE_TAG}" \
  "financial-ai-platform/dashboard=${DASHBOARD_REPOSITORY}:${IMAGE_TAG}"
popd >/dev/null

# Jobs have immutable pod templates; recreate this one for each image revision.
kubectl -n "${NAMESPACE}" delete job database-migrations --ignore-not-found --wait=true
kubectl apply -k "${RENDER_DIR}/k8s-aws"
kubectl -n "${NAMESPACE}" wait --for=condition=complete job/database-migrations --timeout=10m
kubectl -n "${NAMESPACE}" rollout status deployment/gateway --timeout=10m
kubectl -n "${NAMESPACE}" rollout status deployment/dashboard --timeout=10m

echo "AWS rollout complete: ${CLUSTER_NAME} (${AWS_REGION}), image tag ${IMAGE_TAG}"
