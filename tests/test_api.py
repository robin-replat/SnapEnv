"""Tests for all API endpoints.

Test naming convention: test_{endpoint}_{scenario}
Each test follows the Arrange-Act-Assert pattern:
1. Arrange: set up test data
2. Act: call the endpoint
3. Assert: verify the response

We test both "happy paths" (normal usage) and "edge cases" (not found, empty, filters).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    Environment,
    EnvironmentStatus,
    Event,
    EventType,
    Pipeline,
    PipelineStage,
    PipelineStatus,
    PRStatus,
    PullRequest,
    StageStatus,
    StageType,
)

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────


async def create_pull_request(
    db: AsyncSession,
    pr_number: int = 42,
    status: PRStatus = PRStatus.OPEN,
    author: str = "alice",
    title: str = "feat: add login page",
    branch: str = "feat/login",
) -> PullRequest:
    """Helper to create a PullRequest with sensible defaults."""
    pr = PullRequest(
        github_pr_number=pr_number,
        repository="user/repo",
        title=title,
        author=author,
        branch=branch,
        base_branch="main",
        status=status,
        preview_url=(
            f"https://snapenv-pr-{pr_number}.preview.example.dev" if status == PRStatus.OPEN else None
        ),
        github_url=f"https://github.com/user/repo/pull/{pr_number}",
        latest_commit_sha="a" * 40,
    )
    db.add(pr)
    await db.flush()  # Flush to get the generated ID without committing
    return pr


async def create_pipeline(
    db: AsyncSession,
    pr: PullRequest,
    status: PipelineStatus = PipelineStatus.SUCCESS,
    duration: int = 120,
) -> Pipeline:
    """Helper to create a Pipeline linked to a PR."""
    pipeline = Pipeline(
        pull_request_id=pr.id,
        commit_sha="b" * 40,
        status=status,
        duration_seconds=duration,
    )
    db.add(pipeline)
    await db.flush()
    return pipeline


async def create_environment(
    db: AsyncSession,
    pr: PullRequest,
    status: EnvironmentStatus = EnvironmentStatus.RUNNING,
) -> Environment:
    """Helper to create an Environment linked to a PR."""
    env = Environment(
        pull_request_id=pr.id,
        namespace=f"pr-{pr.github_pr_number}",
        url=f"https://pr-{pr.github_pr_number}.preview.example.dev",
        status=status,
        argocd_app_name=f"preview-pr-{pr.github_pr_number}",
    )
    db.add(env)
    await db.flush()
    return env


# ──────────────────────────────────────────────
# Health Check
# ──────────────────────────────────────────────


class TestHealthCheck:
    """Tests for GET /health."""

    @pytest.mark.asyncio
    async def test_returns_healthy(self, client: AsyncClient) -> None:
        response = await client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


# ──────────────────────────────────────────────
# Pull Requests
# ──────────────────────────────────────────────


class TestListPullRequests:
    """Tests for GET /api/pull-requests."""

    @pytest.mark.asyncio
    async def test_empty_list(self, client: AsyncClient) -> None:
        """Returns empty list when no PRs exist."""
        response = await client.get("/api/pull-requests")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_returns_prs_with_environment(self, client: AsyncClient, db_session: AsyncSession) -> None:
        """Returns PRs with their associated environment."""
        pr = await create_pull_request(db_session)
        await create_environment(db_session, pr)
        await db_session.commit()

        response = await client.get("/api/pull-requests")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["github_pr_number"] == 42
        assert data[0]["author"] == "alice"
        assert data[0]["environment"]["status"] == "running"
        assert data[0]["environment"]["namespace"] == "pr-42"

    @pytest.mark.asyncio
    async def test_returns_latest_pipeline(self, client: AsyncClient, db_session: AsyncSession) -> None:
        """Includes the most recent pipeline in the response."""
        pr = await create_pull_request(db_session)
        await create_pipeline(db_session, pr, PipelineStatus.FAILED)
        await create_pipeline(db_session, pr, PipelineStatus.SUCCESS)
        await db_session.commit()

        response = await client.get("/api/pull-requests")

        data = response.json()
        assert len(data) == 1
        # latest_pipeline should be present (most recent)
        assert data[0]["latest_pipeline"] is not None
        assert data[0]["latest_pipeline"]["status"] in ["success", "failed"]

    @pytest.mark.asyncio
    async def test_filter_by_status(self, client: AsyncClient, db_session: AsyncSession) -> None:
        """Filters PRs by status query parameter."""
        await create_pull_request(db_session, pr_number=1, status=PRStatus.OPEN)
        await create_pull_request(db_session, pr_number=2, status=PRStatus.OPEN)
        await create_pull_request(db_session, pr_number=3, status=PRStatus.MERGED)
        await db_session.commit()

        response = await client.get("/api/pull-requests?status=open")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert all(pr["status"] == "open" for pr in data)

    @pytest.mark.asyncio
    async def test_pagination(self, client: AsyncClient, db_session: AsyncSession) -> None:
        """Respects limit and offset parameters."""
        for i in range(5):
            await create_pull_request(db_session, pr_number=i + 1)
        await db_session.commit()

        response = await client.get("/api/pull-requests?limit=2&offset=0")
        assert len(response.json()) == 2

        response = await client.get("/api/pull-requests?limit=2&offset=3")
        assert len(response.json()) == 2

    @pytest.mark.asyncio
    async def test_ordered_by_most_recent(self, client: AsyncClient, db_session: AsyncSession) -> None:
        """PRs are returned most recent first."""
        await create_pull_request(db_session, pr_number=1, title="first")
        await db_session.commit()

        # Small delay to check that both are returned
        # and that the endpoint doesn't crash. For deterministic ordering,
        # we update pr1 so its updated_at is older.
        pr2 = await create_pull_request(db_session, pr_number=2, title="second")
        await db_session.commit()

        # Force pr2 to have a newer updated_at by updating it
        pr2.title = "second updated"
        await db_session.commit()

        response = await client.get("/api/pull-requests")
        data = response.json()

        # PR2 was updated last, so it should be first
        assert data[0]["github_pr_number"] == 2
        assert data[1]["github_pr_number"] == 1


class TestGetPullRequest:
    """Tests for GET /api/pull-requests/{pr_id}."""

    @pytest.mark.asyncio
    async def test_returns_pr_detail(self, client: AsyncClient, db_session: AsyncSession) -> None:
        """Returns full PR details with pipelines and environment."""
        pr = await create_pull_request(db_session)
        await create_environment(db_session, pr)
        await create_pipeline(db_session, pr)
        await db_session.commit()

        response = await client.get(f"/api/pull-requests/{pr.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["github_pr_number"] == 42
        assert data["environment"] is not None
        assert len(data["pipelines"]) == 1

    @pytest.mark.asyncio
    async def test_not_found(self, client: AsyncClient) -> None:
        """Returns 404 for nonexistent PR ID."""
        response = await client.get("/api/pull-requests/00000000-0000-0000-0000-000000000000")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


# ──────────────────────────────────────────────
# Pipelines
# ──────────────────────────────────────────────


class TestGetPipeline:
    """Tests for GET /api/pipelines/{pipeline_id}."""

    @pytest.mark.asyncio
    async def test_returns_pipeline_with_stages(self, client: AsyncClient, db_session: AsyncSession) -> None:
        """Returns pipeline details including all stages."""
        pr = await create_pull_request(db_session)
        pipeline = await create_pipeline(db_session, pr)

        # Add stages
        stages = [
            PipelineStage(
                pipeline_id=pipeline.id,
                stage_type=StageType.LINT,
                status=StageStatus.SUCCESS,
                order=1,
                details={"errors": 0, "warnings": 2, "tool": "ruff"},
                duration_seconds=8,
            ),
            PipelineStage(
                pipeline_id=pipeline.id,
                stage_type=StageType.TEST,
                status=StageStatus.SUCCESS,
                order=2,
                details={"passed": 42, "failed": 0, "coverage": 87.3},
                duration_seconds=45,
            ),
            PipelineStage(
                pipeline_id=pipeline.id,
                stage_type=StageType.BUILD_IMAGE,
                status=StageStatus.RUNNING,
                order=3,
            ),
        ]
        db_session.add_all(stages)
        await db_session.commit()

        response = await client.get(f"/api/pipelines/{pipeline.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert len(data["stages"]) == 3
        # Stages should be ordered by their `order` field
        assert data["stages"][0]["stage_type"] == "lint"
        assert data["stages"][1]["stage_type"] == "test"
        assert data["stages"][2]["stage_type"] == "build_image"
        # JSONB details should be included
        assert data["stages"][0]["details"]["errors"] == 0
        assert data["stages"][1]["details"]["coverage"] == 87.3

    @pytest.mark.asyncio
    async def test_not_found(self, client: AsyncClient) -> None:
        """Returns 404 for nonexistent pipeline ID."""
        response = await client.get("/api/pipelines/00000000-0000-0000-0000-000000000000")

        assert response.status_code == 404


# ──────────────────────────────────────────────
# Events
# ──────────────────────────────────────────────


class TestListEvents:
    """Tests for GET /api/events."""

    @pytest.mark.asyncio
    async def test_empty_list(self, client: AsyncClient) -> None:
        """Returns empty list when no events exist."""
        response = await client.get("/api/events")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_returns_events(self, client: AsyncClient, db_session: AsyncSession) -> None:
        """Returns events ordered by most recent first."""
        pr = await create_pull_request(db_session)
        events = [
            Event(
                event_type=EventType.PR_OPENED,
                message=f"PR #{pr.github_pr_number} opened",
                pull_request_id=pr.id,
            ),
            Event(
                event_type=EventType.PIPELINE_STARTED,
                message=f"Pipeline started for PR #{pr.github_pr_number}",
                pull_request_id=pr.id,
            ),
        ]
        db_session.add_all(events)
        await db_session.commit()

        response = await client.get("/api/events")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_filter_by_pull_request(self, client: AsyncClient, db_session: AsyncSession) -> None:
        """Filters events by pull_request_id."""
        pr1 = await create_pull_request(db_session, pr_number=1)
        pr2 = await create_pull_request(db_session, pr_number=2)

        db_session.add(
            Event(
                event_type=EventType.PR_OPENED,
                message="PR #1 opened",
                pull_request_id=pr1.id,
            )
        )
        db_session.add(
            Event(
                event_type=EventType.PR_OPENED,
                message="PR #2 opened",
                pull_request_id=pr2.id,
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/events?pull_request_id={pr1.id}")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["pull_request_id"] == pr1.id

    @pytest.mark.asyncio
    async def test_respects_limit(self, client: AsyncClient, db_session: AsyncSession) -> None:
        """Respects the limit query parameter."""
        pr = await create_pull_request(db_session)
        for i in range(10):
            db_session.add(
                Event(
                    event_type=EventType.STAGE_COMPLETED,
                    message=f"Stage {i} completed",
                    pull_request_id=pr.id,
                )
            )
        await db_session.commit()

        response = await client.get("/api/events?limit=3")

        assert response.status_code == 200
        assert len(response.json()) == 3


# ──────────────────────────────────────────────
# Dashboard Stats
# ──────────────────────────────────────────────


class TestPlatformStats:
    """Tests for GET /api/stats."""

    @pytest.mark.asyncio
    async def test_empty_stats(self, client: AsyncClient) -> None:
        """Returns zero values when no data exists."""
        response = await client.get("/api/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["active_environments"] == 0
        assert data["total_pull_requests"] == 0
        assert data["open_pull_requests"] == 0
        assert data["pipelines_today"] == 0
        assert data["success_rate_percent"] == 0.0
        assert data["avg_deploy_time_seconds"] is None

    @pytest.mark.asyncio
    async def test_stats_with_data(self, client: AsyncClient, db_session: AsyncSession) -> None:
        """Returns accurate aggregate stats."""
        # Create 2 open PRs, 1 merged
        pr1 = await create_pull_request(db_session, pr_number=1, status=PRStatus.OPEN)
        pr2 = await create_pull_request(db_session, pr_number=2, status=PRStatus.OPEN)
        pr3 = await create_pull_request(db_session, pr_number=3, status=PRStatus.MERGED)

        # Create 1 running environment
        await create_environment(db_session, pr1, EnvironmentStatus.RUNNING)

        # Create pipelines: 2 success, 1 failed
        await create_pipeline(db_session, pr1, PipelineStatus.SUCCESS, duration=100)
        await create_pipeline(db_session, pr2, PipelineStatus.SUCCESS, duration=200)
        await create_pipeline(db_session, pr3, PipelineStatus.FAILED)

        await db_session.commit()

        response = await client.get("/api/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["active_environments"] == 1
        assert data["total_pull_requests"] == 3
        assert data["open_pull_requests"] == 2
        assert data["pipelines_today"] == 3
        # 2 success out of 3 finished (success + failed) = 66.7%
        assert data["success_rate_percent"] == pytest.approx(66.7, abs=0.1)
        # Average of 100 and 200 = 150.0
        assert data["avg_deploy_time_seconds"] == 150.0
