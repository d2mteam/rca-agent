# Docker HITL Agent

Demo local: **yêu cầu → Static Gateway → Planner → Domain Schema → preview → duyệt → Docker → kiểm chứng**.
Python/FastAPI, LangGraph, Pydantic, SQLModel/SQLite, MCP SDK và React/TypeScript. Planner có hai lựa chọn rõ ràng: Codex thật qua `codex exec`, hoặc kịch bản cố định không gọi LLM.

## Cài bằng Docker Compose

Cần **Linux với Docker Engine local và Docker Compose v2+**, quyền truy cập Docker socket, Git và Bash. Không cần cài Python, Node.js, Java hay Codex trên host. Lần đầu cần Internet để tải dependencies/images. Dành khoảng 4 GiB RAM trống cho build và demo. Bản Compose này dùng host networking; chưa hỗ trợ cài trực tiếp trên macOS/Windows/Docker Desktop hoặc Docker host từ xa.

Sau khi clone repository và vào thư mục dự án:

```bash
bash scripts/setup.sh
docker compose -f compose.images.yaml build backend
docker compose -f compose.images.yaml pull nginx postgres
docker compose up -d --build --wait
```

Mở **http://127.0.0.1:8765**. Hai lệnh `compose.images.yaml` chỉ chuẩn bị image; **không chạy `up` với file này**. Workload Nginx/Java/PostgreSQL chỉ được tạo khi duyệt kế hoạch trong UI. Chọn planner **Mẫu cố định**, nhập “Triển khai Compose hiện tại” để thử ngay, không cần tài khoản AI. Console phục vụ cả React UI và API trong một container.

`setup.sh` tạo `.env` theo UID/GID và Docker socket của máy, giữ nguyên nếu file đã tồn tại. `.env.example` giải thích các biến. Đổi `HITL_PORT` trong `.env` nếu 8765 bị chiếm; đổi port Nginx trong Compose của project nếu 8080 bị chiếm. Không chạy đồng thời bản native và bản container trên cùng thư mục dữ liệu. Có thể chuẩn bị một bản độc lập bằng:

```bash
bash scripts/setup.sh .env.other "$PWD/.hitl-other" 18765
docker compose --env-file .env.other -p hitl-console-other up -d --build --wait
```

Hai console trên cùng Docker host vẫn nhìn thấy cùng các workload; dùng tên project khác khi thử độc lập. Thư mục state mặc định `.hitl/` chứa SQLite, Compose chỉnh tay, bản kế hoạch và credentials Codex. Giữ nguyên đường dẫn khi restart; không commit hoặc chia sẻ thư mục này. Nếu chuyển máy, cần nhập lại Compose và lập kế hoạch mới vì snapshot runtime và đường dẫn cũ không còn hợp lệ.

### Bật planner AI

Image đã kèm Codex CLI phiên bản cố định. Đăng nhập tài khoản của người cài, không dùng credentials của người phát triển:

```bash
docker compose exec console codex login --device-auth
docker compose exec console codex login status
```

Làm theo URL/mã đăng nhập được in ra; tài khoản cần cho phép device-code login. Sau đó chọn **Codex AI** trong UI. `HITL_CODEX_MODEL` trong `.env` là tùy chọn; nếu sửa, chạy lại `docker compose up -d`. Credentials nằm ở `.hitl/codex/`, không được đóng vào image. Xem [hướng dẫn xác thực Codex](https://developers.openai.com/codex/auth/).

### Quản lý console

```bash
docker compose ps
docker compose logs -f console
docker compose exec console infra dry-run 'Triển khai Compose hiện tại'
docker compose exec console infra deploy <plan-id>
docker compose down
```

`docker compose down` chỉ dừng/xóa container console; workload đã duyệt và volume PostgreSQL vẫn tồn tại. Chạy `up -d` để mở lại console, giữ lịch sử và policy. Cập nhật source bằng `git pull`, sau đó `docker compose up -d --build --wait`. Nếu có deployment đang chạy, chờ hoàn tất trước khi restart/cập nhật.

Console là công cụ quản trị: Docker socket cho phép điều khiển Docker host. API/UI chỉ bind `127.0.0.1`, chưa có authentication nhiều người dùng; không mở ra mạng ngoài. Container console chạy bằng UID/GID của người cài, thêm group của socket. Trên SELinux, `label=disable` chỉ áp dụng cho console để đọc socket và file bind dùng chung. Các giới hạn của policy áp dụng cho workload do agent tạo.

Host networking giúp verifier gọi đúng port loopback của workload. Thư mục dữ liệu được bind ở **cùng đường dẫn tuyệt đối trên host và trong container** vì Docker daemon đọc bind source từ filesystem của host. Không đổi target thành `/data` độc lập với source. Xem [Docker host networking](https://docs.docker.com/engine/network/drivers/host/) và [bind mounts](https://docs.docker.com/engine/storage/bind-mounts/).

Bản đóng gói đã được thử trên Linux với Docker Engine rootful: build image, chạy UI/API, preview → duyệt → deploy stack bốn container, đạt 55 kiểm tra runtime, giữ Verified State sau restart và nhận diện no-op. Docker rootless chưa được kiểm chứng. Codex CLI trong image đã kiểm tra tương thích tham số; gọi model cần người cài đăng nhập tài khoản riêng.

## Chạy native để phát triển

Cần Docker Engine + Compose, quyền truy cập Docker socket, Node.js 22+, npm và uv. Codex CLI đã đăng nhập nếu dùng planner AI.

```bash
uv sync --locked
npm --prefix frontend ci
npm --prefix frontend run build
bash demo/build.sh
docker pull nginx:1.28-alpine
docker pull postgres:16-alpine
uv run infra serve
```

Mở **http://127.0.0.1:8765**. Chọn Codex AI, tạo preview, đọc diff/spec rồi duyệt. Backend phục vụ luôn bản React đã build. Lần đầu tự sao chép Compose mẫu vào `.hitl/projects/hitl-demo/`; các lần sau giữ chỉnh sửa tại đó.

Yêu cầu demo:

> Triển khai web app gồm Nginx cân bằng tải cho 2 backend Java, dùng chung PostgreSQL có lưu trữ bền vững. Chỉ Nginx mở cổng 8080; backend chỉ khởi động khi database sẵn sàng. Giới hạn RAM mỗi backend 256 MiB.

Sau khi deploy, ghi một bản ghi qua `POST http://127.0.0.1:8080/api/records` với JSON `{"value":"survives-adjust"}`, rồi yêu cầu:

> Tăng backend lên 3 bản sao, giới hạn mỗi bản 512 MiB RAM; giữ nguyên địa chỉ truy cập và dữ liệu hiện có.

Verifier kiểm tra số bản sao, image ID, RAM, port, network, mount, trạng thái/healthcheck; gọi HTTP qua Nginx để kiểm tra database, mọi backend đều nhận request và bản ghi trước adjust vẫn còn. Chỉ Nginx được publish trên loopback; PostgreSQL dùng volume riêng và network internal.

CLI dùng cùng backend và workflow:

```bash
uv run infra dry-run 'Triển khai Compose hiện tại'
uv run infra dry-run 'Triển khai demo' --planner codex
uv run infra deploy <plan-id>
uv run infra adjust 'Tăng backend lên 3 bản sao và RAM 512m'
uv run infra dry-run --file .hitl/projects/hitl-demo/compose.yaml
```

## Giao diện và chỉnh chính sách

UI dùng React Router + Ant Design, chia URL riêng: `/overview`, `/deploy`, `/plans`, `/plans/:id`, `/runtime`, `/policies`. Chi tiết kế hoạch có các tab cấu hình, kiểm chứng và nhật ký; mở trực tiếp URL hoặc reload vẫn hoạt động. Bố cục bảng/tổng quan/chi tiết tham khảo [Spark Web UI](https://spark.apache.org/docs/latest/web-ui.html); màu sắc được cấu hình bằng [Ant Design theme tokens](https://ant.design/docs/react/customize-theme/), với sidebar `#001529`, nền `#f0f2f5`, màu thao tác `#1677ff`.

Ở **Chính sách**, chỉnh bằng form hoặc chọn **Ngôn ngữ tự nhiên**, ví dụ “Tăng giới hạn replicas lên 8; giữ nguyên luật khác”. Codex chỉ tạo đề xuất. Preview hiển thị từng trường trước/sau và hash; phải duyệt riêng mới lưu phiên bản mới vào SQLite.

Các trường chỉnh được: port bị chặn, chặn publish database/cache, giới hạn replicas/RAM, từ khóa bị chặn, động từ và đối tượng hạ tầng được nhận diện. Luật port áp dụng cho cả port host lẫn port container. Muốn cho phép publish database còn phải tắt luật chặn loại service tương ứng. RAM phải có giới hạn tường minh, không được dùng `0` để vượt qua giới hạn policy. Phạm vi host local, no privileged/socket/host network và không xóa tài nguyên vẫn là giới hạn của executor, không được tắt bằng lời nhắn.

Policy áp dụng chung cho host. Khi duyệt phiên bản mới, kế hoạch chờ duyệt chuyển sang `stale`; executor kiểm tra lại revision ngay trước apply. Không đổi policy khi có deployment đang chạy. Runtime đang chạy không tự bị sửa; muốn đưa runtime về luật mới phải lập kế hoạch triển khai. Spec của từng kế hoạch giữ snapshot policy dùng khi kiểm tra. Chỉnh một bản nháp policy làm mất preview đang hiển thị và cần preview lại.

## Hai lớp kiểm tra

- **Trước LLM — `backend/hitl/gateway.py`:** luật cố định kiểm tra phạm vi, thao tác phá hủy, port nhạy cảm và truy cập host. Yêu cầu bị chặn không gọi model hoặc Docker. Đây là bộ luật cho phạm vi demo, chưa phải bộ hiểu mọi cách diễn đạt ngôn ngữ tự nhiên.
- **Sau LLM — `backend/hitl/domain.py`, `policy.py`:** allowlist Compose, `docker compose config`, Pydantic kiểm tra cấu trúc/ngữ nghĩa: image, replicas, tên, dependencies có thật/không vòng lặp, port không trùng và tài nguyên thuộc project. Lỗi trả về Planner, tối đa 3 lần tạo/kiểm tra đề xuất.
- **`InfrastructureSpec` là cấu hình mong muốn chuẩn hóa:** Compose thực thi được render từ spec, preview có tab spec, verifier đọc spec và Verified State lưu spec. Compose chỉnh tay là đầu vào nhập lại qua cùng pipeline.

LangGraph giữ checkpoint SQLite tại bước phê duyệt. Planner chỉ có ba công cụ đọc được cho phép; custom MCP server chứa executor và verifier. Executor tự kiểm tra phê duyệt đã lưu, hash artifact/spec, source và runtime trước khi chạy Compose. `approved: true` trong đầu ra AI không cấp quyền. Bản preview đóng băng image ID và nội dung file bind; sửa source hoặc runtime làm kế hoạch cũ hết hiệu lực.

Nhật ký lưu cả thành công và thất bại. **Apply thành công khác với Verify đạt.** Verified State chỉ cập nhật khi mọi kiểm tra đạt. Nếu ứng dụng đã tắt nên không đọc được baseline dữ liệu, preview cảnh báo; có thể duyệt khởi động lại nhưng chưa xác nhận bảo toàn dữ liệu. Tạo preview mới khi ứng dụng hoạt động để kiểm chứng trạng thái hiện tại; việc này không chứng minh hồi tố lần khởi động trước không mất dữ liệu.

## Kiểm thử

```bash
uv run pytest -q
uv run ruff check backend scripts
npm --prefix frontend test
npm --prefix frontend run build
# Có thay đổi Docker của riêng project hitl-demo; duyệt các kế hoạch demo qua API:
uv run python scripts/demo_smoke.py --approve --planner codex
```

Smoke test thực tế kiểm tra chặn trước LLM, từ chối không đổi Docker, sửa Compose làm approval cũ stale, deploy/adjust, lưu dữ liệu và nhận diện no-op. Báo cáo: `.hitl/demo-report.json`. Unit tests kiểm tra schema, giới hạn retry, approval/hash, checkpoint restart và giữ Verified State khi lỗi. Tests Java chạy trong `demo/build.sh`; H2 tests không thay cho kiểm chứng PostgreSQL thật.

## Phạm vi MVP

Một Docker host local; image phải có sẵn; project được đăng ký trong `.hitl/projects/` và tên bắt đầu `hitl-`. Chỉ hỗ trợ subset Compose đã allowlist; không tự chạy build, shell tùy ý, destroy, rollback hoặc xóa volume. MCP kết nối trực tiếp custom tool server qua stdio, không dùng Docker MCP Gateway chính thức. Khi process bị ngắt giữa apply, ghi nhận chưa hoàn tất và yêu cầu kế hoạch mới, không tự replay thao tác Docker.

HTTP probes hiện dành riêng cho API của fixture Java. Credentials trong Compose chỉ dùng demo local. UI/API bind loopback, chưa phải dịch vụ nhiều người dùng. Trạng thái, log và cấu hình nằm trong `.hitl/`; không đưa thư mục này lên Git.
