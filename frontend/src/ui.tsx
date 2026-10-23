import { useCallback, useEffect, useState } from "react";
import { Alert, Spin, Tag } from "antd";
import { api } from "./api";
import type { Plan } from "./api";

export function useLoad<T>(path: string) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((v) => v + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api<T>(path, { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [path, revision]);
  return { data, setData, loading, error, reload };
}
export const labels: Record<string, [string, string]> = {
  awaiting_approval: ["Chờ phê duyệt", "gold"],
  approved: ["Đã phê duyệt", "blue"],
  applying: ["Đang triển khai", "processing"],
  applied: ["Đã triển khai · chờ kiểm chứng", "blue"],
  verifying: ["Đang kiểm chứng", "processing"],
  verified: ["Kiểm chứng đạt", "success"],
  failed: ["Triển khai thất bại", "error"],
  verification_failed: ["Kiểm chứng chưa đạt", "error"],
  rejected: ["Đã từ chối", "default"],
  stale: ["Cần lập lại kế hoạch", "warning"],
  no_changes: ["Không có thay đổi", "success"],
  needs_clarification: ["Cần làm rõ", "warning"],
};
export function Status({
  plan,
}: {
  plan: Pick<Plan, "status" | "verification">;
}) {
  const status =
    plan.status === "failed" && plan.verification
      ? "verification_failed"
      : plan.status;
  const [label, color] = labels[status] || [status, "default"];
  return <Tag color={color}>{label}</Tag>;
}
export function ErrorNotice({ error }: { error?: string }) {
  return error ? (
    <Alert type="error" showIcon title={error} className="notice" />
  ) : null;
}
export function Loading() {
  return (
    <div className="loading">
      <Spin />
    </div>
  );
}
export function Code({ value }: { value: unknown }) {
  return (
    <pre className="code-view">
      {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
    </pre>
  );
}
export const date = (value?: string) =>
  value ? new Date(value).toLocaleString("vi-VN") : "—";
