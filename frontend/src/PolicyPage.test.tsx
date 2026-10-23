import { beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PolicyPage from "./PolicyPage";
const policy = {
  blocked_ports: [5432],
  block_sensitive_services: true,
  max_replicas: 6,
  max_memory_mib: 4096,
  blocked_terms: [],
  allowed_actions: ["triển khai"],
  infrastructure_terms: ["backend"],
};
const preview = {
  id: "policy-test",
  status: "awaiting_approval",
  created_at: "2026-10-23T10:00:00Z",
  request: "",
  base_revision: 1,
  before: policy,
  after: { ...policy, max_replicas: 8 },
  changes: [{ field: "max_replicas", before: 6, after: 8 }],
  plan_hash: "immutable-policy-hash",
  diff: "",
};
let calls: { path: string; body: Record<string, unknown> }[];
beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, options?: RequestInit) => {
      const body = options?.body ? JSON.parse(String(options.body)) : {};
      calls.push({ path, body });
      const data =
        path === "/api/policy"
          ? { revision: 1, policy, hash: "current" }
          : path === "/api/policy/changes"
            ? { changes: [] }
            : path === "/api/policy/preview"
              ? preview
              : path.endsWith("/approve")
                ? { ...preview, status: "approved" }
                : { ...preview, status: "rejected" };
      return { ok: true, json: async () => data };
    }),
  );
});
it("form policy changes require preview and any subsequent edit removes approval", async () => {
  render(<PolicyPage />);
  const input = await screen.findByRole("spinbutton", {
    name: "Replicas tối đa / service",
  });
  fireEvent.change(input, { target: { value: "8" } });
  await userEvent.click(
    screen.getByRole("button", { name: "Xem trước chính sách" }),
  );
  await screen.findByRole("button", { name: "Duyệt & áp dụng chính sách" });
  expect(
    calls.find((c) => c.path === "/api/policy/preview")?.body.policy,
  ).toMatchObject({ max_replicas: 8 });
  expect(calls.some((c) => c.path.endsWith("/approve"))).toBe(false);
  fireEvent.change(input, { target: { value: "9" } });
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "Duyệt & áp dụng chính sách" }),
    ).not.toBeInTheDocument(),
  );
});
it("natural-language edits still require explicit immutable-hash approval", async () => {
  render(<PolicyPage />);
  await screen.findByRole("tab", { name: "Ngôn ngữ tự nhiên" });
  await userEvent.click(screen.getByRole("tab", { name: "Ngôn ngữ tự nhiên" }));
  fireEvent.change(
    screen.getByRole("textbox", { name: "Yêu cầu đổi chính sách" }),
    { target: { value: "Tăng giới hạn replicas lên 8" } },
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Xem trước chính sách" }),
  );
  await screen.findByRole("button", { name: "Duyệt & áp dụng chính sách" });
  expect(calls.find((c) => c.path === "/api/policy/preview")?.body).toEqual({
    request: "Tăng giới hạn replicas lên 8",
  });
  expect(calls.some((c) => c.path.endsWith("/approve"))).toBe(false);
  await userEvent.click(
    screen.getByRole("button", { name: "Duyệt & áp dụng chính sách" }),
  );
  await screen.findByText("Đã áp dụng chính sách.");
  expect(calls.find((c) => c.path.endsWith("/approve"))?.body).toEqual({
    plan_hash: "immutable-policy-hash",
  });
});
