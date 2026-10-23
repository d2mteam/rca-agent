"""Local-only MCP tool process. Approval is enforced inside the write tool."""

from mcp.server.fastmcp import FastMCP

from .runtime import DockerRuntime
from .settings import Settings
from .store import Store

settings = Settings().prepare()
store = Store(settings.data_dir)
runtime = DockerRuntime(settings, store)
mcp = FastMCP("HITL Docker Tools", log_level="WARNING")


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
def inspect_project(project: str) -> dict:
    """Read containers, project networks and volumes without changing Docker."""
    return runtime.inspect(project)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
def recent_logs(project: str, service: str | None = None, tail: int = 80) -> dict:
    """Read bounded recent container logs for a single project."""
    return runtime.logs(project, service, tail)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
def last_verification(project: str) -> dict:
    """Read the most recent successful verification, if one exists."""
    return store.verified(project) or {}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False})
def docker_apply_plan(plan_id: str, plan_hash: str) -> dict:
    """Apply a persisted, human-approved immutable plan. No arbitrary commands or YAML accepted."""
    return runtime.apply(plan_id, plan_hash)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
def docker_verify_plan(plan_id: str) -> dict:
    """Inspect Docker and probe the application against a saved plan, without changing Docker."""
    return runtime.verify(store.get(plan_id))


if __name__ == "__main__":
    mcp.run(transport="stdio")
