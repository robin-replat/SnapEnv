#!/bin/bash
# setup-cluster.sh — Creates the local k3d cluster and installs core components.
#
# What this script does:
# 1. Creates a k3d cluster (K8s running inside Docker)
# 2. Installs Nginx Ingress Controller (routes external traffic to services)
# 3. Installs kube-prometheus-stack (Prometheus + Grafana)
# 4. Installs ArgoCD with an HTTP Ingress (no port-forward needed)
# 5. Prints access information
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
echo -e "\n${GREEN}[1/5] Creating k3d cluster '${CLUSTER_NAME}'...${NC}"

k3d cluster delete ${CLUSTER_NAME} 2>/dev/null || true
k3d cluster create --config k3d-config.yaml

echo "Waiting for cluster to be ready..."
kubectl wait --for=condition=Ready nodes --all --timeout=60s

# ── Step 2: Install Nginx Ingress Controller ──
echo -e "\n${GREEN}[2/5] Installing Nginx Ingress Controller...${NC}"

helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update

helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx \
  --create-namespace \
  --set controller.publishService.enabled=true \
  --wait --timeout 120s

kubectl wait --namespace ingress-nginx \
  --for=condition=Ready pod \
  --selector=app.kubernetes.io/component=controller \
  --timeout=120s

# ── Step 3: Install kube-prometheus-stack ─────
echo -e "\n${GREEN}[3/5] Installing Prometheus + Grafana (kube-prometheus-stack)...${NC}"

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

# grafana.adminPassword: default login is admin/admin (local cluster only).
# sidecar.dashboards.searchNamespace=ALL: Grafana picks up dashboard ConfigMaps
# from any namespace, including the default namespace where our app runs.
helm install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace \
  --set grafana.adminPassword=admin \
  --set grafana.sidecar.dashboards.enabled=true \
  --set grafana.sidecar.dashboards.searchNamespace=ALL \
  --wait --timeout 300s

# ── Step 4: Install ArgoCD ────────────────────
echo -e "\n${GREEN}[4/5] Installing ArgoCD...${NC}"

kubectl create namespace argocd 2>/dev/null || true
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml --server-side

echo "Waiting for ArgoCD to be ready (this may take a minute)..."
kubectl wait --namespace argocd \
  --for=condition=Ready pod \
  --selector=app.kubernetes.io/name=argocd-server \
  --timeout=180s

# Enable HTTP mode via the params ConfigMap — the documented ArgoCD way to set
# server flags. Avoids patching the Deployment args directly, which races with
# pod termination and breaks across ArgoCD versions.
kubectl patch configmap argocd-cmd-params-cm -n argocd \
  --type merge \
  -p '{"data":{"server.insecure":"true"}}'

kubectl rollout restart deployment/argocd-server -n argocd
echo "Waiting for ArgoCD to restart in HTTP mode..."
kubectl rollout status deployment/argocd-server -n argocd --timeout=300s

# Create HTTP Ingress for ArgoCD — accessible at http://argocd.localhost
kubectl apply -f - <<'EOF'
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: argocd-ingress
  namespace: argocd
spec:
  ingressClassName: nginx
  rules:
    - host: argocd.localhost
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: argocd-server
                port:
                  number: 80
EOF

# ── Step 5: Print access info ─────────────────
echo -e "\n${GREEN}[5/5] Setup complete!${NC}"

ARGOCD_PASSWORD=$(kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath="{.data.password}" | base64 -d)

echo -e "\n${CYAN}═══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  Cluster is ready!${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
echo ""
echo "  Kubernetes dashboard:  kubectl get pods -A"
echo ""
echo "  ArgoCD UI:             http://argocd.localhost"
echo "    User:     admin"
echo "    Password: ${ARGOCD_PASSWORD}"
echo ""
echo "  Grafana UI:            http://grafana.localhost"
echo "    User:     admin"
echo "    Password: admin"
echo ""
echo "  Next steps:"
echo "    1. Generate local Helm secrets from your .env file:"
echo "       make helm-secrets"
echo ""
echo "    2. Build and deploy the app:"
echo "       make k8s-deploy"
echo ""
echo "    3. Access the app at: http://snapenv.localhost"
echo ""
