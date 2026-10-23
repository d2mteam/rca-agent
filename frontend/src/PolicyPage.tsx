import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
} from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { api } from "./api";
import { Code, date, ErrorNotice, Loading, useLoad } from "./ui";

export interface Policy {
  blocked_ports: number[];
  block_sensitive_services: boolean;
  max_replicas: number;
  max_memory_mib: number;
  blocked_terms: string[];
  allowed_actions: string[];
  infrastructure_terms: string[];
}
interface Current {
  revision: number;
  hash: string;
  policy: Policy;
}
interface Change {
  id: string;
  created_at: string;
  request: string;
  base_revision: number;
  before: Policy;
  after: Policy;
  changes: { field: string; before: unknown; after: unknown }[];
  plan_hash: string;
  diff: string;
  status: string;
}
export default function PolicyPage() {
  const current = useLoad<Current>("/policy");
  const history = useLoad<{ changes: Change[] }>("/policy/changes");
  const [form] = Form.useForm<Policy>();
  const [source, setSource] = useState("form");
  const [request, setRequest] = useState(
    "Tăng giới hạn replicas lên 8 và RAM tối đa mỗi service lên 2048 MiB; giữ nguyên các luật khác.",
  );
  const [preview, setPreview] = useState<Change>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (current.data) form.setFieldsValue(current.data.policy);
  }, [current.data, form]);
  async function create() {
    setBusy(true);
    setError("");
    try {
      const body =
        source === "form"
          ? { policy: await form.validateFields() }
          : { request };
      setPreview(
        await api<Change>("/policy/preview", {
          method: "POST",
          body: JSON.stringify(body),
        }),
      );
      history.reload();
    } catch (e) {
      setError((e as Error).message || "Kiểm tra lại các trường chính sách.");
    } finally {
      setBusy(false);
    }
  }
  async function decide(decision: string) {
    if (!preview) return;
    setBusy(true);
    setError("");
    try {
      setPreview(
        await api<Change>(`/policy/changes/${preview.id}/${decision}`, {
          method: "POST",
          body: JSON.stringify({ plan_hash: preview.plan_hash }),
        }),
      );
      current.reload();
      history.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function edit() {
    setPreview(undefined);
    setError("");
  }
  if (!current.data)
    return current.error ? <ErrorNotice error={current.error} /> : <Loading />;
  return (
    <>
      <div className="page-title">
        <div>
          <h1>Chính sách</h1>
          <p>Luật dùng chung cho Static Gateway và Domain Schema.</p>
        </div>
        <Button
          icon={<ReloadOutlined />}
          onClick={() => {
            current.reload();
            history.reload();
            edit();
          }}
        >
          Làm mới
        </Button>
      </div>
      <ErrorNotice error={error || current.error || history.error} />
      <Card className="section-card">
        <Descriptions
          size="small"
          column={2}
          items={[
            {
              key: "revision",
              label: "Phiên bản đang dùng",
              children: <Tag color="blue">v{current.data.revision}</Tag>,
            },
            {
              key: "scope",
              label: "Phạm vi",
              children: "Tất cả project trên host local",
            },
          ]}
        />
        <p className="help-text">
          Đổi chính sách không thay đổi Docker. Kế hoạch chưa triển khai phải
          tạo lại preview. Host network, privileged, Docker socket và thao tác
          xóa vẫn nằm ngoài phạm vi executor.
        </p>
      </Card>
      <Card className="editor-card">
        <Tabs
          activeKey={source}
          onChange={(key) => {
            setSource(key);
            edit();
          }}
          items={[
            {
              key: "form",
              label: "Chỉnh bằng form",
              children: (
                <Form
                  form={form}
                  layout="vertical"
                  disabled={busy}
                  onValuesChange={edit}
                >
                  <div className="policy-limits">
                    <Form.Item
                      label="Replicas tối đa / service"
                      name="max_replicas"
                      rules={[{ required: true }]}
                    >
                      <InputNumber
                        min={1}
                        max={32}
                        aria-label="Replicas tối đa / service"
                      />
                    </Form.Item>
                    <Form.Item
                      label="RAM tối đa / service (MiB)"
                      name="max_memory_mib"
                      rules={[{ required: true }]}
                    >
                      <InputNumber
                        min={64}
                        max={32768}
                        aria-label="RAM tối đa / service (MiB)"
                      />
                    </Form.Item>
                    <Form.Item
                      label="Chặn publish database/cache"
                      name="block_sensitive_services"
                      valuePropName="checked"
                    >
                      <Switch aria-label="Chặn publish database/cache" />
                    </Form.Item>
                  </div>
                  <Form.Item
                    label="Port bị chặn (cả host và container)"
                    name="blocked_ports"
                    getValueProps={(values: number[]) => ({
                      value: (values || []).map(String),
                    })}
                    getValueFromEvent={(values: string[]) => values.map(Number)}
                    rules={[
                      {
                        validator: (_, value: number[]) =>
                          value?.every(
                            (v) => Number.isInteger(v) && v >= 1 && v <= 65535,
                          )
                            ? Promise.resolve()
                            : Promise.reject(
                                new Error("Port phải là số từ 1 đến 65535."),
                              ),
                      },
                    ]}
                  >
                    <Select
                      mode="tags"
                      tokenSeparators={[",", " "]}
                      aria-label="Port bị chặn"
                    />
                  </Form.Item>
                  <Form.Item
                    label="Từ khóa bổ sung bị chặn"
                    name="blocked_terms"
                  >
                    <Select
                      mode="tags"
                      tokenSeparators={[","]}
                      aria-label="Từ khóa bị chặn"
                      placeholder="Nhập từ khóa rồi Enter"
                    />
                  </Form.Item>
                  <Form.Item
                    label="Động từ hạ tầng được nhận diện"
                    name="allowed_actions"
                    rules={[{ required: true }]}
                  >
                    <Select
                      mode="tags"
                      tokenSeparators={[","]}
                      aria-label="Động từ hạ tầng"
                    />
                  </Form.Item>
                  <Form.Item
                    label="Đối tượng hạ tầng được nhận diện"
                    name="infrastructure_terms"
                    rules={[{ required: true }]}
                  >
                    <Select
                      mode="tags"
                      tokenSeparators={[","]}
                      aria-label="Đối tượng hạ tầng"
                    />
                  </Form.Item>
                </Form>
              ),
            },
            {
              key: "request",
              label: "Ngôn ngữ tự nhiên",
              children: (
                <>
                  <label htmlFor="policy-request" className="field-label">
                    Yêu cầu đổi chính sách
                  </label>
                  <Input.TextArea
                    id="policy-request"
                    value={request}
                    rows={5}
                    disabled={busy}
                    onChange={(e) => {
                      setRequest(e.target.value);
                      edit();
                    }}
                  />
                  <p className="help-text">
                    Codex tạo đề xuất. Chỉ bản policy trong preview được áp dụng
                    sau khi bạn duyệt.
                  </p>
                </>
              ),
            },
            {
              key: "history",
              label: "Lịch sử thay đổi",
              children: (
                <Table
                  rowKey="id"
                  size="small"
                  dataSource={history.data?.changes || []}
                  pagination={{ pageSize: 8 }}
                  columns={[
                    {
                      title: "ID",
                      dataIndex: "id",
                      render: (id: string, change: Change) => (
                        <Button type="link" onClick={() => setPreview(change)}>
                          {id.slice(0, 12)}
                        </Button>
                      ),
                    },
                    { title: "Phiên bản gốc", dataIndex: "base_revision" },
                    {
                      title: "Thay đổi",
                      dataIndex: "changes",
                      render: (changes: Change["changes"]) =>
                        changes.map((c) => c.field).join(", "),
                    },
                    {
                      title: "Trạng thái",
                      dataIndex: "status",
                      render: (s: string) => (
                        <Tag
                          color={
                            s === "approved"
                              ? "success"
                              : s === "awaiting_approval"
                                ? "gold"
                                : "default"
                          }
                        >
                          {s === "approved"
                            ? "Đã áp dụng"
                            : s === "awaiting_approval"
                              ? "Chờ duyệt"
                              : s === "rejected"
                                ? "Đã từ chối"
                                : "Không đổi"}
                        </Tag>
                      ),
                    },
                    { title: "Tạo lúc", dataIndex: "created_at", render: date },
                  ]}
                />
              ),
            },
          ]}
        />
        {source !== "history" && (
          <div className="action-bar">
            <span>Không áp dụng trực tiếp từ form hoặc lời nhắn.</span>
            <Button
              type="primary"
              loading={busy}
              disabled={source === "request" && !request.trim()}
              onClick={() => void create()}
            >
              Xem trước chính sách
            </Button>
          </div>
        )}
      </Card>
      {preview && (
        <Card
          className="section-card"
          title={`Preview chính sách · ${preview.id.slice(0, 12)}`}
          extra={
            <Tag>
              v{preview.base_revision} → v{preview.base_revision + 1}
            </Tag>
          }
        >
          <Table
            rowKey="field"
            pagination={false}
            size="small"
            dataSource={preview.changes}
            columns={[
              { title: "Trường", dataIndex: "field", width: 190 },
              {
                title: "Trước",
                dataIndex: "before",
                render: (v) => <Code value={v} />,
              },
              {
                title: "Sau",
                dataIndex: "after",
                render: (v) => <Code value={v} />,
              },
            ]}
          />
          <div className="hash-line">
            <span>Hash</span>
            <code>{preview.plan_hash}</code>
          </div>
          {preview.status === "awaiting_approval" ? (
            <>
              <Alert
                type="warning"
                showIcon
                title="Áp dụng cho tất cả project. Kế hoạch đang chờ duyệt sẽ hết hiệu lực; container đang chạy giữ nguyên."
              />
              <div className="action-bar">
                <span>Chính sách mới được code kiểm tra trước khi lưu.</span>
                <Space>
                  <Button onClick={() => void decide("reject")} disabled={busy}>
                    Từ chối chính sách
                  </Button>
                  <Button
                    type="primary"
                    loading={busy}
                    onClick={() => void decide("approve")}
                  >
                    Duyệt & áp dụng chính sách
                  </Button>
                </Space>
              </div>
            </>
          ) : (
            <Alert
              type={preview.status === "approved" ? "success" : "info"}
              title={
                preview.status === "approved"
                  ? "Đã áp dụng chính sách."
                  : preview.status === "rejected"
                    ? "Đã từ chối. Chính sách không đổi."
                    : "Không có thay đổi chính sách."
              }
            />
          )}
        </Card>
      )}
    </>
  );
}
