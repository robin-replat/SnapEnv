"""Celery task definitions.

Tasks are enqueued by the API when GitHub webhook events arrive and
executed here asynchronously by the worker.

Tasks to implement:
  - handle_pr_opened(pr_id)   → provision preview environment via ArgoCD
  - handle_pr_updated(pr_id)  → re-deploy on new commit
  - handle_pr_closed(pr_id)   → destroy preview environment
  - run_pipeline_stage(...)   → execute a single pipeline stage and record the result
"""
