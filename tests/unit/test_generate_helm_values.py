"""Unit tests for local Helm values generation helpers."""

from scripts.generate_helm_values import (
    IN_CLUSTER_ARGOCD_SERVER,
    _helm_argocd_server,
)


def test_helm_argocd_server_defaults_to_cluster_service() -> None:
    assert _helm_argocd_server("") == IN_CLUSTER_ARGOCD_SERVER


def test_helm_argocd_server_rewrites_loopback_urls_for_pods() -> None:
    assert _helm_argocd_server("https://localhost:8080") == IN_CLUSTER_ARGOCD_SERVER
    assert _helm_argocd_server("http://127.0.0.1:8080") == IN_CLUSTER_ARGOCD_SERVER


def test_helm_argocd_server_keeps_non_loopback_url() -> None:
    assert _helm_argocd_server("http://argocd-server.argocd.svc.cluster.local") == (
        "http://argocd-server.argocd.svc.cluster.local"
    )
