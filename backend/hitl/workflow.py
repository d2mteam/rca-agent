from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class State(TypedDict, total=False):
    plan_id: str
    project: str
    request: str
    compose_yaml: str | None
    planner: str
    mode: str
    status: str
    approved: bool
    applied: bool


def build_graph(service, checkpointer):
    async def prepare(state):
        plan = await service.build_plan(state)
        return {"status": plan["status"]}

    def approval(state):
        plan = service.store.get(state["plan_id"])
        # This node is replayed on resume; it deliberately has no side effects.
        answer = interrupt(
            {"plan_id": plan["id"], "plan_hash": plan["plan_hash"], "summary": plan.get("summary")}
        )
        accepted = answer.get("decision") == "approve" and service.store.approved(
            plan["id"], plan["plan_hash"]
        )
        return {"approved": accepted}

    async def execute(state):
        result = await service.execute(state["plan_id"])
        return {"applied": result.get("applied", False)}

    async def verify(state):
        await service.verify(state["plan_id"])
        return {}

    def finalize(state):
        service.finalize(state["plan_id"])
        return {"status": service.store.get(state["plan_id"])["status"]}

    graph = StateGraph(State)
    graph.add_node("observe_plan_validate_preview", prepare)
    graph.add_node("human_approval", approval)
    graph.add_node("recheck_and_execute", execute)
    graph.add_node("verify_runtime", verify)
    graph.add_node("save_verified_state", finalize)
    graph.add_edge(START, "observe_plan_validate_preview")
    graph.add_conditional_edges(
        "observe_plan_validate_preview",
        lambda s: "human_approval" if s["status"] == "awaiting_approval" else END,
    )
    graph.add_conditional_edges("human_approval", lambda s: "recheck_and_execute" if s["approved"] else END)
    graph.add_conditional_edges("recheck_and_execute", lambda s: "verify_runtime" if s["applied"] else END)
    graph.add_edge("verify_runtime", "save_verified_state")
    graph.add_edge("save_verified_state", END)
    return graph.compile(checkpointer=checkpointer)
