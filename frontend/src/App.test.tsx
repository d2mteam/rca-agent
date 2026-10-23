import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import type { Plan } from "./api";

const proposed: Plan = {
  id: "plan-immutable-001",
  project: "hitl-demo",
  status: "awaiting_approval",
  plan_hash: "a".repeat(64),
  request: "Triển khai web app",
  planner: "template",
  created_at: "2026-10-23T10:00:00Z",
  compose_yaml: "services:\n  backend:\n    image: demo:1\n",
  input_yaml: "services:\n  backend:\n    image: editable:1\n",
  infrastructure_spec: {
    project: "hitl-demo",
    services: { backend: { image_id: "sha256:frozen-image", replicas: 2 } },
  },
  previous_yaml: "",
  diff: "+services:\n+  backend:\n+    image: demo:1",
  changes: [{ service: "backend", action: "create", detail: "2 replicas" }],
  checks: [{ name: "Backend health", expected: "healthy" }],
  events: [],
};
let calls: { path: string; options?: RequestInit }[];
let currentPlan: Plan;

beforeEach(() => {
  window.history.replaceState({}, "", "/deploy");
  calls = [];
  currentPlan = structuredClone(proposed);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, options?: RequestInit) => {
      calls.push({ path, options });
      let body: unknown;
      if (path === "/api/health")
        body = {
          status: "ok",
          docker: { available: true },
          planner: "template",
        };
      else if (path === "/api/projects")
        body = { projects: [{ name: "hitl-demo" }] };
      else if (path === "/api/projects/hitl-demo")
        body = {
          name: "hitl-demo",
          compose_yaml: "services: {}",
          runtime: [],
          verified_state: null,
        };
      else if (path === "/api/plans" && options?.method === "POST")
        body = currentPlan;
      else if (path === "/api/plans") body = { plans: [] };
      else if (path.endsWith("/reject"))
        body = currentPlan = { ...currentPlan, status: "rejected" };
      else if (path.endsWith("/approve"))
        body = currentPlan = { ...currentPlan, status: "applied" };
      else if (path === `/api/plans/${proposed.id}`)
        body =
          currentPlan.status === "applied"
            ? (currentPlan = {
                ...currentPlan,
                status: "verified",
                verification: {
                  passed: true,
                  checks: [
                    {
                      name: "Backend healthy",
                      status: "pass",
                      expected: 2,
                      actual: 2,
                    },
                  ],
                },
              })
            : currentPlan;
      else throw new Error(`Unexpected fetch: ${path}`);
      return { ok: true, status: 200, json: async () => body };
    }),
  );
});

async function preview() {
  render(<App />);
  await screen.findByText("Engine đã kết nối");
  await userEvent.click(
    screen.getByRole("button", { name: "Tạo bản xem trước" }),
  );
  await screen.findByRole("button", { name: "Phê duyệt & triển khai" });
}

describe("human approval boundary", () => {
  it("creates a preview on a separate URL and rejection never approves", async () => {
    await preview();
    expect(window.location.pathname).toBe(`/plans/${proposed.id}`);
    expect(calls.filter((call) => call.path.endsWith("/approve"))).toHaveLength(
      0,
    );
    await userEvent.click(screen.getByRole("button", { name: "Từ chối" }));
    await screen.findByText(
      "Bạn đã từ chối kế hoạch này. Docker không được thay đổi.",
    );
    expect(calls.filter((call) => call.path.endsWith("/approve"))).toHaveLength(
      0,
    );
    expect(
      JSON.parse(
        String(calls.find((c) => c.path.endsWith("/reject"))?.options?.body),
      ),
    ).toEqual({ plan_hash: proposed.plan_hash });
  });
  it("shows the frozen spec, then edits the original Compose on a new page without old approval", async () => {
    await preview();
    await userEvent.click(
      screen.getByRole("tab", { name: "InfrastructureSpec" }),
    );
    expect(screen.getByText(/sha256:frozen-image/)).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("link", { name: "Chỉnh sửa thành kế hoạch mới" }),
    );
    const editor = await screen.findByRole("textbox", {
      name: "Cấu hình Compose mong muốn",
    });
    await waitFor(() => expect(editor).toHaveValue(proposed.input_yaml));
    fireEvent.change(editor, { target: { value: "services: {changed: {}}" } });
    expect(
      screen.queryByRole("button", { name: "Phê duyệt & triển khai" }),
    ).not.toBeInTheDocument();
    expect(calls.filter((call) => call.path.endsWith("/approve"))).toHaveLength(
      0,
    );
  });
  it("sends the hash only on approval and polls applied through verified", async () => {
    await preview();
    await userEvent.click(
      screen.getByRole("button", { name: "Phê duyệt & triển khai" }),
    );
    await screen.findByText("Kiểm chứng đạt", {}, { timeout: 4000 });
    expect(
      JSON.parse(
        String(calls.find((c) => c.path.endsWith("/approve"))?.options?.body),
      ),
    ).toEqual({ plan_hash: proposed.plan_hash });
    await userEvent.click(
      screen.getByRole("tab", { name: "Kiểm chứng (1/1)" }),
    );
    expect(await screen.findByText("Backend healthy")).toBeInTheDocument();
  });
  it("keeps overview separate from deployment input", async () => {
    window.history.replaceState({}, "", "/overview");
    render(<App />);
    await screen.findByRole("heading", { name: "Tổng quan" });
    expect(
      screen.queryByRole("textbox", { name: "Yêu cầu triển khai" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Phê duyệt & triển khai" }),
    ).not.toBeInTheDocument();
  });
});
