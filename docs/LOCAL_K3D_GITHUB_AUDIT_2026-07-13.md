# GitHub-to-local-k3d audit - SnapEnv - 2026-07-13

## Verdict

The supported local target is:

```text
GitHub webhooks + GitHub Actions + GHCR
        -> public tunnel
        -> SnapEnv running on local k3d
        -> ArgoCD creates preview namespaces in local k3d
```

This is a valid architecture for development when no cloud Kubernetes cluster is
available. It is not offline-local, but the only Kubernetes cluster required is k3d on
the developer machine.

## Required runtime setup

1. Run the platform in k3d with `make cluster-create`, `make helm-secrets`, and
   `make k8s-deploy`.
2. Expose the local ingress with a tunnel:

   ```bash
   ngrok http 80 --host-header=snapenv.localhost
   ```

3. Configure the GitHub webhook payload URL as:

   ```text
   https://xxxx.ngrok-free.app/api/webhooks/github
   ```

4. Enable both webhook event groups:
   - Pull requests
   - Workflow runs
5. Set `GITHUB_WEBHOOK_SECRET` locally to the same webhook secret.
6. Set `ARGOCD_TOKEN` so the worker can create/delete real ArgoCD Applications.
7. If the GHCR package is private, set `GHCR_USERNAME` and `GHCR_TOKEN` so preview
   namespaces receive an image pull secret.

## Fixes applied for this mode

- GitHub Actions now publishes a multi-arch preview image:
  `linux/amd64,linux/arm64`.
- The worker passes `image.repository`, `image.tag`, and `image.pullPolicy` to ArgoCD.
- The worker passes database credentials to the preview chart, because previews still
  reuse the SnapEnv platform chart during the prototype phase.
- The worker optionally passes registry credentials to create an image pull secret for
  private GHCR packages.
- The worker disables monitoring resources for preview Applications so they stay inside
  their PR namespace and do not create extra Grafana/ServiceMonitor objects.
- Missing ArgoCD credentials now fail the deployment instead of marking an environment as
  ready without creating it.
- Local Helm values generation rewrites loopback ArgoCD URLs to the in-cluster service:
  `http://argocd-server.argocd.svc.cluster.local`.

## Remaining prototype caveat

The same Helm chart is still used for the SnapEnv platform and the preview workload.
That means each preview namespace deploys another copy of SnapEnv's API, worker,
PostgreSQL, and Redis. This is acceptable for a controlled demo, but the future product
should split the platform chart from a minimal preview workload chart.

## Current status

**GO for GitHub-triggered previews into local k3d**, provided ngrok/cloudflared,
`ARGOCD_TOKEN`, and GHCR pull access are configured.
