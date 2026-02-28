#!/bin/bash
# setup-cluster.sh — Creates the local k3d cluster and installs core components.
#
# What this script does:
# 1. Creates a k3d cluster (K8s running inside Docker)
# 2. Installs Nginx Ingress Controller (routes external traffic to services)
# 3. Installs ArgoCD (GitOps continuous deployment)
# 4. Prints access information
#
# Usage: ./scripts/setup-cluster.sh

set -euo pipefail

CLUSTER_NAME="snapenv"
CYAN='\033[0;36m'
GREEN='\033[0;32m'
NC='\033[0m'

echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  SnapEnv — Local Kubernetes Cluster Setup${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"

# ── Step 1: Create k3d cluster ────────────────
echo -e "\n${GREEN}[1/4] Creating k3d cluster '${CLUSTER_NAME}'...${NC}"

# Delete existing cluster if it exists
k3d cluster delete ${CLUSTER_NAME} 2>/dev/null || true

k3d cluster create --config k3d-config.yaml

echo "Waiting for cluster to be ready..."
kubectl wait --for=condition=Ready nodes --all --timeout=60s

# ── Step 2: Install Nginx Ingress Controller ──
echo -e "\n${GREEN}[2/4] Installing Nginx Ingress Controller...${NC}"

# Add the ingress-nginx Helm repository
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update

# Install nginx ingress into its own namespace
helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx \
  --create-namespace \
  --set controller.publishService.enabled=true \
  --wait --timeout 120s

echo "Waiting for Ingress Controller to be ready..."
kubectl wait --namespace ingress-nginx \
  --for=condition=Ready pod \
  --selector=app.kubernetes.io/component=controller \
  --timeout=120s

# ── Step 3: Install ArgoCD ────────────────────
echo -e "\n${GREEN}[3/4] Installing ArgoCD...${NC}"

# Create the argocd namespace and install ArgoCD
kubectl create namespace argocd 2>/dev/null || true
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml --server-side

echo "Waiting for ArgoCD to be ready (this may take a minute)..."
kubectl wait --namespace argocd \
  --for=condition=Ready pod \
  --selector=app.kubernetes.io/name=argocd-server \
  --timeout=180s

# ── Step 4: Print access info ─────────────────
echo -e "\n${GREEN}[4/4] Setup complete!${NC}"

# Get the ArgoCD initial admin password
# As it is a dev project ArgoCD admin password is printed. It is not the best practice,
# but it is going to be easier for users.
ARGOCD_PASSWORD=$(kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath="{.data.password}" | base64 -d)

echo -e "\n${CYAN}═══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  Cluster is ready!${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
echo ""
echo "  Kubernetes dashboard:  kubectl get pods -A"
echo ""
echo "  ArgoCD UI:"
echo "    Run:      kubectl port-forward svc/argocd-server -n argocd 8080:443"
echo "    Open:     https://localhost:8080"
echo "    User:     admin"
echo "    Password: ${ARGOCD_PASSWORD}"
echo ""
echo "  Next steps:"
echo "    1. Generate local Helm secrets from your .env file:"
echo "       make helm-secrets"
echo ""
echo "    2. Build and import your Docker image:"
echo "       docker build -t snapenv:local -f .docker/Dockerfile.api ."
echo "       k3d image import snapenv:local -c ${CLUSTER_NAME}"
echo ""
echo "    3. Deploy the app with Helm:"
echo "       helm install snapenv ./infra/helm/snapenv \\"
echo "         -f ./infra/helm/snapenv/values-local.yaml \\"
echo "         --set image.repository=snapenv \\"
echo "         --set image.tag=local \\"
echo "         --set image.pullPolicy=Never"
echo ""
echo "    4. Access the app at: http://snapenv.localhost"
echo ""