"""Generate the ignored local Helm values file from ``.env``.

JSON is valid YAML, avoids hand-written escaping, and keeps the values grouped
under the keys consumed by the chart templates.
"""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
OUTPUT_FILE = ROOT / "infra" / "helm" / "snapenv" / "values-local.yaml"


def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        raise SystemExit(f"Missing {path}. Copy .env.example to .env first.")

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def main() -> None:
    env = _read_env(ENV_FILE)
    values = {
        "postgresql": {
            "auth": {
                "username": env.get("POSTGRES_USER", "snapenv"),
                "password": env.get("POSTGRES_PASSWORD", "snapenv"),
                "database": env.get("POSTGRES_DB", "snapenv"),
            }
        },
        "env": {
            "GITHUB_WEBHOOK_SECRET": env.get("GITHUB_WEBHOOK_SECRET", ""),
            "DEBUG": env.get("DEBUG", "false"),
            "LOG_LEVEL": env.get("LOG_LEVEL", "INFO"),
        },
        "argocd": {
            "server": env.get("ARGOCD_SERVER") or "http://argocd-server.argocd.svc.cluster.local",
            "token": env.get("ARGOCD_TOKEN", ""),
        },
        "github": {
            "token": env.get("GITHUB_TOKEN", ""),
            "repository": env.get("GITHUB_REPOSITORY", ""),
        },
        "preview": {
            "domain": env.get("PREVIEW_DOMAIN", "localhost"),
            "helmChartPath": env.get("HELM_CHART_PATH", "infra/helm/snapenv"),
        },
    }

    OUTPUT_FILE.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    with suppress(OSError):
        OUTPUT_FILE.chmod(0o600)


if __name__ == "__main__":
    main()
