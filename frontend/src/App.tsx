import { createContext, useContext, useEffect, useState } from "react";
import {
  Alert,
  Badge,
  Breadcrumb,
  Button,
  Card,
  Col,
  Collapse,
  ConfigProvider,
  Descriptions,
  Empty,
  Form,
  Input,
  Layout,
  Menu,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import {
  AppstoreOutlined,
  CloudServerOutlined,
  DeploymentUnitOutlined,
  FileSearchOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import {
  BrowserRouter,
  Link,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { api, dockerReady, isRunning } from "./api";
import type { Health, Plan, Project, ProjectState } from "./api";
import {
  Code,
  date,
  ErrorNotice,
  labels,
  Loading,
  Status,
  useLoad,
} from "./ui";
import PolicyPage from "./PolicyPage";

const DEPLOY =
  "Triển khai web app gồm Nginx cân bằng tải cho 2 backend Java, dùng chung PostgreSQL có lưu trữ bền vững. Chỉ Nginx mở cổng 8080; backend chỉ khởi động khi database sẵn sàng.";
const ADJUST =
  "Tăng backend lên 3 bản sao, giới hạn mỗi bản 512 MiB RAM; giữ nguyên địa chỉ truy cập và dữ liệu hiện có.";
const ProjectContext = createContext("hitl-demo");
const nav = [
  { key: "/overview", label: "Tổng quan", icon: <AppstoreOutlined /> },
  { key: "/deploy", label: "Tạo triển khai", icon: <PlusOutlined /> },
  { key: "/plans", label: "Kế hoạch", icon: <FileSearchOutlined /> },
  { key: "/runtime", label: "Runtime", icon: <CloudServerOutlined /> },
  {
    key: "/policies",
    label: "Chính sách",
    icon: <SafetyCertificateOutlined />,
  },
];

function PlanTable({
  plans,
  compact = false,
}: {
  plans: Plan[];
  compact?: boolean;
}) {
  return (
    <Table
      rowKey="id"
      size="small"
      dataSource={plans}
      pagination={{ pageSize: compact ? 6 : 12, hideOnSinglePage: true }}
      scroll={{ x: 760 }}
      columns={[
        {
          title: "Plan ID",
          dataIndex: "id",
          width: 145,
          render: (id: string) => (
            <Link className="mono" to={`/plans/${id}`}>
              {id.slice(0, 12)}
            </Link>
          ),
        },
        {
          title: "Yêu cầu",
          dataIndex: "request",
          render: (value: string) => (
            <span className="request-cell">{value || "Compose thủ công"}</span>
          ),
        },
        {
          title: "Planner",
          dataIndex: "planner",
          width: 105,
          render: (p: string) =>
            p === "codex"
              ? "Codex AI"
              : p === "manual"
                ? "Compose"
                : "Kịch bản",
        },
        {
          title: "Trạng thái",
          width: 190,
          render: (_, p: Plan) => <Status plan={p} />,
        },
        { title: "Tạo lúc", dataIndex: "created_at", width: 175, render: date },
      ]}
    />
  );
}
function Overview() {
  const project = useContext(ProjectContext);
  const plans = useLoad<{ plans: Plan[] }>("/plans");
  const runtime = useLoad<ProjectState>(`/projects/${project}`);
  const items = plans.data?.plans.filter((p) => p.project === project) || [];
  const containers =
    (runtime.data?.runtime as { containers?: unknown[] })?.containers || [];
  return (
    <>
      <div className="page-title">
        <div>
          <h1>Tổng quan</h1>
          <p>Trạng thái project và các lần triển khai gần nhất.</p>
        </div>
        <Button
          icon={<ReloadOutlined />}
          onClick={() => {
            plans.reload();
            runtime.reload();
          }}
        >
          Làm mới
        </Button>
      </div>
      <ErrorNotice error={plans.error || runtime.error} />
      <Row gutter={[16, 16]} className="metrics">
        {[
          ["Containers", containers.length],
          [
            "Chờ phê duyệt",
            items.filter((p) => p.status === "awaiting_approval").length,
          ],
          ["Đang xử lý", items.filter((p) => isRunning(p.status)).length],
          [
            "Đã kiểm chứng",
            items.filter((p) => p.status === "verified").length,
          ],
        ].map(([title, value]) => (
          <Col xs={12} lg={6} key={title}>
            <Card>
              <Statistic title={title} value={value} />
            </Card>
          </Col>
        ))}
      </Row>
      <Card title="Pipeline triển khai" className="section-card">
        <div className="pipeline">
          <span>Static Gateway</span>
          <b>→</b>
          <span>Planner</span>
          <b>→</b>
          <span>Domain Schema</span>
          <b>→</b>
          <span>Phê duyệt</span>
          <b>→</b>
          <span>Docker</span>
          <b>→</b>
          <span>Verifier</span>
        </div>
      </Card>
      <Card
        title="Kế hoạch gần đây"
        extra={<Link to="/plans">Xem tất cả</Link>}
      >
        <PlanTable plans={items} compact />
      </Card>
    </>
  );
}
function Plans() {
  const project = useContext(ProjectContext);
  const { data, error, loading, reload } = useLoad<{ plans: Plan[] }>("/plans");
  const [status, setStatus] = useState("all");
  const items =
    data?.plans.filter(
      (p) => p.project === project && (status === "all" || p.status === status),
    ) || [];
  return (
    <>
      <div className="page-title">
        <div>
          <h1>Kế hoạch</h1>
          <p>Mở một kế hoạch để xem cấu hình, phê duyệt và bằng chứng.</p>
        </div>
        <Link to="/deploy">
          <Button type="primary" icon={<PlusOutlined />}>
            Tạo triển khai
          </Button>
        </Link>
      </div>
      <ErrorNotice error={error} />
      <Card
        title={
          <Space>
            Trạng thái{" "}
            <Select
              aria-label="Lọc trạng thái"
              value={status}
              onChange={setStatus}
              style={{ width: 210 }}
              options={[
                { value: "all", label: "Tất cả" },
                ...Object.entries(labels)
                  .filter(([key]) => key !== "verification_failed")
                  .map(([value, [label]]) => ({ value, label })),
              ]}
            />
          </Space>
        }
        extra={
          <Button icon={<ReloadOutlined />} onClick={reload}>
            Làm mới
          </Button>
        }
        loading={loading && !data}
      >
        <PlanTable plans={items} />
      </Card>
    </>
  );
}
function Deploy() {
  const project = useContext(ProjectContext);
  const navigate = useNavigate();
  const [query] = useSearchParams();
  const from = query.get("from");
  const projectData = useLoad<ProjectState>(`/projects/${project}`);
  const [source, setSource] = useState("request");
  const [request, setRequest] = useState(DEPLOY);
  const [yaml, setYaml] = useState("");
  const [planner, setPlanner] = useState("codex");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (projectData.data && !from) setYaml(projectData.data.compose_yaml);
  }, [projectData.data, from]);
  useEffect(() => {
    if (!from) return;
    const controller = new AbortController();
    api<Plan>(`/plans/${from}`, { signal: controller.signal })
      .then((p) => {
        setYaml(p.input_yaml ?? p.compose_yaml);
        setRequest(p.request);
        setSource("compose");
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [from]);
  async function preview() {
    setBusy(true);
    setError("");
    try {
      const plan = await api<Plan>("/plans", {
        method: "POST",
        body: JSON.stringify({
          project,
          planner,
          mode: "adjust",
          ...(source === "request" ? { request } : { compose_yaml: yaml }),
        }),
      });
      navigate(`/plans/${plan.id}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="page-title">
        <div>
          <h1>Tạo triển khai</h1>
          <p>Mô tả thay đổi hoặc chỉnh Compose. Bước này chỉ tạo preview.</p>
        </div>
      </div>
      <ErrorNotice error={error || projectData.error} />
      <Card
        className="editor-card"
        title="Cấu hình mong muốn"
        extra={<Tag color="blue">DRY RUN</Tag>}
      >
        {from && (
          <Alert
            type="info"
            title="Bạn đang tạo kế hoạch mới từ cấu hình cũ. Bản duyệt trước không áp dụng cho chỉnh sửa này."
            className="notice"
          />
        )}
        <Tabs
          activeKey={source}
          onChange={setSource}
          items={[
            {
              key: "request",
              label: "Ngôn ngữ tự nhiên",
              children: (
                <>
                  <Form layout="vertical">
                    <Form.Item label="Yêu cầu triển khai">
                      <Input.TextArea
                        aria-label="Yêu cầu triển khai"
                        value={request}
                        onChange={(e) => setRequest(e.target.value)}
                        rows={6}
                        disabled={busy}
                      />
                    </Form.Item>
                    <Space wrap className="examples">
                      <Button size="small" onClick={() => setRequest(DEPLOY)}>
                        Web app 3 lớp
                      </Button>
                      <Button size="small" onClick={() => setRequest(ADJUST)}>
                        Scale lên 3 replicas
                      </Button>
                    </Space>
                    <Form.Item label="Planner">
                      <Select
                        aria-label="Planner"
                        value={planner}
                        onChange={setPlanner}
                        options={[
                          { value: "codex", label: "Codex AI" },
                          {
                            value: "template",
                            label: "Kịch bản demo (không dùng LLM)",
                          },
                        ]}
                      />
                    </Form.Item>
                  </Form>
                </>
              ),
            },
            {
              key: "compose",
              label: "Compose YAML",
              children: (
                <Input.TextArea
                  aria-label="Cấu hình Compose mong muốn"
                  className="yaml-editor"
                  value={yaml}
                  onChange={(e) => setYaml(e.target.value)}
                  rows={20}
                  disabled={busy}
                />
              ),
            },
          ]}
        />
        <div className="action-bar">
          <span>Static Gateway → Domain Schema → InfrastructureSpec</span>
          <Button
            type="primary"
            onClick={() => void preview()}
            loading={busy}
            disabled={!(source === "request" ? request.trim() : yaml.trim())}
          >
            Tạo bản xem trước
          </Button>
        </div>
      </Card>
    </>
  );
}
function PlanDetail() {
  const { id } = useParams();
  const { data: plan, setData, error, reload } = useLoad<Plan>(`/plans/${id}`);
  const [busy, setBusy] = useState(false);
  const [decisionError, setDecisionError] = useState("");
  useEffect(() => {
    if (!plan || !isRunning(plan.status)) return;
    const timer = setTimeout(reload, 1300);
    return () => clearTimeout(timer);
  }, [plan, reload]);
  async function decide(decision: string) {
    if (!plan) return;
    setBusy(true);
    setDecisionError("");
    try {
      setData(
        await api<Plan>(`/plans/${id}/${decision}`, {
          method: "POST",
          body: JSON.stringify({ plan_hash: plan.plan_hash }),
        }),
      );
      reload();
    } catch (e) {
      setDecisionError((e as Error).message);
      reload();
    } finally {
      setBusy(false);
    }
  }
  if (!plan) return error ? <ErrorNotice error={error} /> : <Loading />;
  const passed =
    plan.verification?.checks.filter((c) => c.status === "pass").length || 0;
  return (
    <>
      <div className="page-title">
        <div>
          <h1>
            Kế hoạch <code>{plan.id.slice(0, 12)}</code>
          </h1>
          <p>{plan.request || "Chỉnh Compose thủ công"}</p>
        </div>
        <Status plan={plan} />
      </div>
      <ErrorNotice error={error || decisionError || plan.error} />
      {plan.warnings?.map((w) => (
        <Alert key={w} title={w} type="warning" className="notice" showIcon />
      ))}
      <Card className="section-card">
        <Descriptions
          size="small"
          column={{ xs: 1, md: 3 }}
          items={[
            { key: "project", label: "Project", children: plan.project },
            {
              key: "planner",
              label: "Planner",
              children: plan.planner === "codex" ? "Codex AI" : plan.planner,
            },
            { key: "time", label: "Tạo lúc", children: date(plan.created_at) },
          ]}
        />
        <div className="hash-line">
          <span>Hash đã đóng băng</span>
          <code>{plan.plan_hash}</code>
        </div>
        <div className="action-bar">
          <Space>
            <Link to={`/deploy?from=${plan.id}`}>
              <Button disabled={isRunning(plan.status)}>
                Chỉnh sửa thành kế hoạch mới
              </Button>
            </Link>
            <Button icon={<ReloadOutlined />} onClick={reload}>
              Làm mới
            </Button>
          </Space>
          {plan.status === "awaiting_approval" && (
            <Space>
              <Button onClick={() => void decide("reject")} disabled={busy}>
                Từ chối
              </Button>
              <Button
                type="primary"
                loading={busy}
                onClick={() => void decide("approve")}
              >
                Phê duyệt & triển khai
              </Button>
            </Space>
          )}
        </div>
        {plan.status === "rejected" && (
          <Alert
            title="Bạn đã từ chối kế hoạch này. Docker không được thay đổi."
            type="info"
          />
        )}
      </Card>
      <Card className="detail-card">
        <Tabs
          defaultActiveKey="config"
          items={[
            {
              key: "config",
              label: "Cấu hình & thay đổi",
              children: (
                <>
                  <Table
                    size="small"
                    rowKey="service"
                    pagination={false}
                    dataSource={plan.changes}
                    columns={[
                      { title: "Service", dataIndex: "service" },
                      {
                        title: "Thao tác",
                        dataIndex: "action",
                        render: (s: string) => <Tag>{s}</Tag>,
                      },
                      { title: "Ảnh hưởng", dataIndex: "detail" },
                    ]}
                  />
                  <Tabs
                    items={[
                      {
                        key: "diff",
                        label: "Diff",
                        children: (
                          <Code
                            value={plan.diff || "Không có thay đổi cấu hình."}
                          />
                        ),
                      },
                      {
                        key: "new",
                        label: "Compose mới",
                        children: <Code value={plan.compose_yaml} />,
                      },
                      {
                        key: "before",
                        label: "Compose trước",
                        children: <Code value={plan.previous_yaml} />,
                      },
                      {
                        key: "spec",
                        label: "InfrastructureSpec",
                        children: <Code value={plan.infrastructure_spec} />,
                      },
                    ]}
                  />
                </>
              ),
            },
            {
              key: "verify",
              label: `Kiểm chứng${plan.verification ? ` (${passed}/${plan.verification.checks.length})` : ""}`,
              children: plan.verification ? (
                <>
                  <Alert
                    title={`${passed}/${plan.verification.checks.length} TIÊU CHÍ ĐẠT`}
                    type={plan.verification.passed ? "success" : "warning"}
                    className="notice"
                  />
                  <Table
                    rowKey="name"
                    size="small"
                    dataSource={plan.verification.checks}
                    pagination={{ pageSize: 12 }}
                    scroll={{ x: 800 }}
                    columns={[
                      { title: "Tiêu chí", dataIndex: "name", width: 290 },
                      {
                        title: "Mong muốn",
                        dataIndex: "expected",
                        render: (v) => <Code value={v} />,
                      },
                      {
                        title: "Thực tế",
                        dataIndex: "actual",
                        render: (v) => <Code value={v} />,
                      },
                      {
                        title: "Kết quả",
                        dataIndex: "status",
                        width: 110,
                        render: (s) => (
                          <Tag
                            color={
                              s === "pass"
                                ? "success"
                                : s === "skip"
                                  ? "warning"
                                  : "error"
                            }
                          >
                            {s === "pass"
                              ? "Đạt"
                              : s === "skip"
                                ? "Bỏ qua"
                                : "Chưa đạt"}
                          </Tag>
                        ),
                      },
                    ]}
                  />
                </>
              ) : (
                <Empty description="Chưa có kết quả kiểm chứng" />
              ),
            },
            {
              key: "journal",
              label: "Nhật ký",
              children: (
                <Table
                  rowKey={(e) => String(e.id)}
                  size="small"
                  pagination={{ pageSize: 12 }}
                  dataSource={plan.events || []}
                  columns={[
                    {
                      title: "Thời gian",
                      dataIndex: "created_at",
                      render: date,
                      width: 180,
                    },
                    { title: "Bước", dataIndex: "kind", width: 170 },
                    { title: "Nội dung", dataIndex: "message" },
                  ]}
                  expandable={{
                    expandedRowRender: (e) => <Code value={e.data} />,
                    rowExpandable: (e) =>
                      Boolean(e.data && Object.keys(e.data as object).length),
                  }}
                />
              ),
            },
          ]}
        />
      </Card>
    </>
  );
}
interface Container {
  id: string;
  name: string;
  service: string;
  status: string;
  health: string;
  memory: number;
  image: string;
  ports: Record<string, unknown>;
  networks: string[];
  mounts: unknown[];
}
function Runtime() {
  const project = useContext(ProjectContext);
  const { data, error, loading, reload } = useLoad<ProjectState>(
    `/projects/${project}`,
  );
  const runtime = data?.runtime as { containers?: Container[] };
  const verified = data?.verified_state as
    | {
        plan_id: string;
        created_at: string;
        payload: { infrastructure_spec: unknown };
      }
    | undefined;
  return (
    <>
      <div className="page-title">
        <div>
          <h1>Runtime</h1>
          <p>Snapshot chỉ đọc từ Docker, giới hạn trong project {project}.</p>
        </div>
        <Button icon={<ReloadOutlined />} onClick={reload} loading={loading}>
          Làm mới runtime
        </Button>
      </div>
      <ErrorNotice error={error} />
      <Card title="Containers">
        <Table
          rowKey="name"
          size="small"
          dataSource={runtime?.containers || []}
          scroll={{ x: 800 }}
          pagination={{ pageSize: 12 }}
          columns={[
            { title: "Container", dataIndex: "name" },
            { title: "Service", dataIndex: "service" },
            {
              title: "Trạng thái",
              dataIndex: "status",
              render: (s) => (
                <Tag color={s === "running" ? "success" : "default"}>{s}</Tag>
              ),
            },
            {
              title: "Health",
              dataIndex: "health",
              render: (h) => (
                <Tag color={h === "healthy" ? "success" : "warning"}>
                  {h || "—"}
                </Tag>
              ),
            },
            {
              title: "RAM limit",
              dataIndex: "memory",
              render: (n) => (n ? `${n / 1048576} MiB` : "Không giới hạn"),
            },
            {
              title: "Published ports",
              dataIndex: "ports",
              render: (p) => <Code value={p} />,
            },
          ]}
          expandable={{
            expandedRowRender: (c) => (
              <Descriptions
                column={1}
                size="small"
                items={[
                  {
                    key: "image",
                    label: "Image ID",
                    children: <code>{c.image}</code>,
                  },
                  {
                    key: "net",
                    label: "Networks",
                    children: c.networks.join(", "),
                  },
                  {
                    key: "mounts",
                    label: "Mounts",
                    children: <Code value={c.mounts} />,
                  },
                ]}
              />
            ),
          }}
        />
      </Card>
      <Card title="Verified State" className="section-card">
        <Typography.Paragraph type="secondary">
          Lần kiểm chứng đạt gần nhất, có thể khác runtime hiện tại.
        </Typography.Paragraph>
        {verified ? (
          <>
            <Descriptions
              size="small"
              items={[
                {
                  key: "plan",
                  label: "Kế hoạch",
                  children: (
                    <Link to={`/plans/${verified.plan_id}`}>
                      {verified.plan_id.slice(0, 12)}
                    </Link>
                  ),
                },
                {
                  key: "time",
                  label: "Kiểm chứng lúc",
                  children: date(verified.created_at),
                },
              ]}
            />
            <Collapse
              ghost
              items={[
                {
                  key: "spec",
                  label: "InfrastructureSpec đã kiểm chứng",
                  children: (
                    <Code value={verified.payload.infrastructure_spec} />
                  ),
                },
              ]}
            />
          </>
        ) : (
          <Empty description="Chưa có trạng thái đã kiểm chứng" />
        )}
      </Card>
    </>
  );
}
function Shell() {
  const [project, setProject] = useState("hitl-demo");
  const location = useLocation();
  const navigate = useNavigate();
  const health = useLoad<Health>("/health");
  const projects = useLoad<{ projects: Project[] }>("/projects");
  const section = "/" + (location.pathname.split("/")[1] || "overview");
  const [collapsed, setCollapsed] = useState(false);
  return (
    <ProjectContext.Provider value={project}>
      <Layout className="console-shell">
        <Layout.Sider
          width={224}
          breakpoint="lg"
          collapsedWidth={64}
          collapsed={collapsed}
          onCollapse={setCollapsed}
          className="console-sidebar"
        >
          <Link to="/overview" className="console-brand">
            <DeploymentUnitOutlined />
            <span>
              HITL <small>Docker Console</small>
            </span>
          </Link>
          <div className="menu-caption">PROJECT OPERATIONS</div>
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={[section]}
            onClick={({ key }) => navigate(key)}
            items={nav}
          />
          <div className="sidebar-bottom">Docker · localhost</div>
        </Layout.Sider>
        <Layout>
          <Layout.Header className="console-header">
            <Breadcrumb
              items={[
                { title: "Workspace" },
                { title: project },
                {
                  title:
                    nav.find((n) => n.key === section)?.label || "Kế hoạch",
                },
              ]}
            />
            <Space size="large">
              <Select
                aria-label="Project hiện tại"
                value={project}
                onChange={(value) => {
                  setProject(value);
                  navigate("/overview");
                }}
                options={(
                  projects.data?.projects || [{ name: "hitl-demo" }]
                ).map((p) => ({ value: p.name, label: p.name }))}
              />
              <Badge
                status={dockerReady(health.data?.docker) ? "success" : "error"}
                text={
                  dockerReady(health.data?.docker)
                    ? "Engine đã kết nối"
                    : "Docker chưa sẵn sàng"
                }
              />
            </Space>
          </Layout.Header>
          <Layout.Content className="console-content">
            <Routes>
              <Route path="/" element={<Navigate to="/overview" replace />} />
              <Route path="/overview" element={<Overview key={project} />} />
              <Route path="/deploy" element={<Deploy key={project} />} />
              <Route path="/plans" element={<Plans key={project} />} />
              <Route path="/plans/:id" element={<PlanDetail />} />
              <Route path="/runtime" element={<Runtime key={project} />} />
              <Route path="/policies" element={<PolicyPage />} />
              <Route
                path="*"
                element={<Empty description="Không tìm thấy trang" />}
              />
            </Routes>
          </Layout.Content>
          <Layout.Footer className="console-footer">
            HITL · Local Docker
          </Layout.Footer>
        </Layout>
      </Layout>
    </ProjectContext.Provider>
  );
}
export default function App() {
  return (
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: "#1677ff",
          colorSuccess: "#389e0d",
          colorWarning: "#d48806",
          colorError: "#cf1322",
          colorBgLayout: "#f0f2f5",
          borderRadius: 4,
          fontFamily: '"Be Vietnam Pro", sans-serif',
          fontSize: 13,
        },
        components: {
          Table: { cellPaddingBlockSM: 10, headerBg: "#fafafa" },
          Layout: { siderBg: "#001529", headerBg: "#fff" },
          Menu: { darkItemBg: "#001529", darkItemSelectedBg: "#1677ff" },
        },
      }}
    >
      <BrowserRouter>
        <Shell />
      </BrowserRouter>
    </ConfigProvider>
  );
}
