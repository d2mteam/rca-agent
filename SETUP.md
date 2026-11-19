# Hướng dẫn cài đặt và chạy

Tài liệu này dành cho người mới: cài project, tải và chuẩn bị dữ liệu, chạy thử
công cụ, chạy agent và chấm điểm. Giải thích chi tiết về bài toán, dữ liệu và từng
công cụ nằm trong [README.md](README.md).

## 1. Tóm tắt project

- **Bài toán.** Khi một hệ thống phần mềm nhiều máy gặp sự cố, cần tìm ra sự cố bắt
  đầu **lúc nào**, ở **máy nào**, và **vì sao**. Bài toán này gọi là root cause
  analysis (RCA).
- **Dữ liệu.** Dùng bộ benchmark [OpenRCA](https://github.com/microsoft/OpenRCA)
  của Microsoft (ICLR 2025). Mỗi câu hỏi đưa ra một khoảng 30 phút có sự cố, kèm
  metric, log và trace của hệ thống. Project dùng hệ **Bank** (136 câu hỏi) và đã
  thử trên hệ **Telecom** (51 câu hỏi).
- **Cách làm.** Không bắt LLM đọc dữ liệu thô.
  - Project có sẵn các công cụ dòng lệnh. Mỗi công cụ so dữ liệu với lúc bình
    thường, rồi in ra một bảng ngắn.
  - Một agent LLM (Claude Sonnet chạy trong Claude Code) tự gọi các công cụ, nêu
    giả thuyết, kiểm từng giả thuyết, rồi nộp đáp án.
  - Agent được đọc một "hồ sơ hệ thống", mô tả hệ thống lúc khỏe trông thế nào.
- **Chấm điểm.** Theo luật của OpenRCA. Thời điểm được lệch tối đa 60 giây; tên máy
  và lý do phải đúng từng chữ. Một câu chỉ tính là giải đúng khi mọi trường được
  hỏi đều đúng.
- **Kết quả hiện tại (2026-11-19).** Trên 96 câu Bank chưa từng dùng để sửa công cụ:

  | Phương pháp | Giải đúng |
  |---|---:|
  | Agent | 64/96 |
  | z-score | 25/96 |
  | Đoán ngẫu nhiên | 7/96 |

## 2. Cần chuẩn bị

| Thứ cần | Ghi chú |
|---|---|
| Linux hoặc macOS | Các lệnh dưới đây viết cho shell bash. |
| [uv](https://docs.astral.sh/uv/) | Trình quản lý Python. Nếu máy chưa có Python 3.13 trở lên, uv tự tải về. |
| Ổ đĩa trống | **Bank:** khoảng 31 GB (file zip 2,8 GB + giải nén 26 GB + Parquet 2,5 GB). **Telecom:** thêm khoảng 22 GB nếu giải nén đủ 15 ngày; ít hơn nếu chỉ giải nén một số ngày. |
| RAM | Mỗi lệnh dùng tới khoảng 2–3 GB. Nếu chạy nhiều agent cùng lúc, tính thêm 2 GB cho mỗi agent. |
| Claude Code | Chỉ cần nếu muốn chạy agent (mục 6). |

## 3. Cài project

```bash
git clone https://github.com/d2mteam/rca-agent.git
cd rca-agent
uv sync --group baro        # cài thư viện, kể cả thư viện BARO (fse-baro)
uv run pytest -q            # kiểm tra: phải thấy "50 passed"
```

Nhóm `baro` là bắt buộc. Ngoài BARO, nó còn kéo theo NumPy, mà các công cụ khác cũng
cần tới.

## 4. Tải và chuẩn bị dữ liệu

### 4.1 Tải

Tải dữ liệu từ thư mục
[Google Drive của OpenRCA](https://drive.google.com/drive/folders/1wGiEnu4OkWrjPxfx5ZTROnU37-5UDoPM?usp=drive_link).
Chỉ cần `Bank.zip`; thêm `Telecom.zip` nếu muốn thử Telecom. `Market.zip` không
dùng. Đặt các file vào thư mục `data/` của repo:

```text
rca-agent/
  data/
    Bank.zip
    Telecom.zip
```

Thư mục `data/` đã nằm trong `.gitignore`, nên dữ liệu không bị đưa lên git.

### 4.2 Giải nén

```bash
unzip -q data/Bank.zip -d data/openrca          # 10 ngày, khoảng 26 GB
```

Với Telecom, có thể giải nén cả archive (15 ngày, khoảng 18 GB), hoặc chỉ những
ngày cần dùng. Ví dụ, lệnh dưới đây chỉ giải nén:
- hai ngày chứa 15 câu đã thử (26/5 và 27/5);
- ba ngày dùng để viết hồ sơ Telecom (22/5, 28/5 và 31/5).

```bash
unzip -q data/Telecom.zip 'Telecom/query.csv' 'Telecom/record.csv' \
  'Telecom/telemetry/2020_05_22/*' 'Telecom/telemetry/2020_05_26/*' \
  'Telecom/telemetry/2020_05_27/*' 'Telecom/telemetry/2020_05_28/*' \
  'Telecom/telemetry/2020_05_31/*' \
  -d data/openrca
```

Giải nén xong, cấu trúc thư mục trông như sau:

```text
data/openrca/Bank/
  query.csv                  # câu hỏi
  record.csv                 # đáp án
  telemetry/2021_03_09/
    metric/metric_app.csv  metric/metric_container.csv
    trace/trace_span.csv
    log/log_service.csv
  telemetry/...              # các ngày khác
```

### 4.3 Chuẩn hóa sang Parquet

Bước này đổi CSV của từng ngày sang Parquet, với cùng tên cột cho mọi hệ (xem
README, mục 2.2). Chỉ cần làm một lần. Bước này có thể mất vài phút cho mỗi ngày,
vì riêng file trace của một ngày Bank đã khoảng 2,4 GB.

```bash
uv run python -m rca prepare                     # Bank: mọi ngày đã giải nén
uv run python -m rca --system Telecom prepare    # Telecom
uv run python -m rca prepare 2021_03_09          # hoặc chỉ một ngày
```

Mỗi ngày xong, lệnh in ra số dòng của từng bảng. Kết quả nằm ở
`data/parquet/<hệ>/<ngày>/`. Ngày nào đã có file Parquet thì lệnh bỏ qua, nên chạy
lại không mất công.

### 4.4 Kiểm tra

```bash
uv run python -m rca tool question bank-114
```

Lệnh này in ra đề bài của câu bank-114. Nếu thấy đề bài, khoảng thời gian và danh
sách máy, thì dữ liệu đã sẵn sàng.

Lệnh `python -m rca cases` liệt kê mọi câu hỏi **kèm đáp án**. Đừng chạy nó trong
phiên làm việc của agent.

## 5. Dùng thử các công cụ bằng tay

Mọi công cụ có dạng `uv run python -m rca tool <công cụ> <mã câu hỏi> ...`. Mã câu
hỏi là `bank-001` … `bank-136` và `telecom-001` … `telecom-051`, đánh số theo thứ
tự dòng trong `query.csv`.

```bash
T="uv run python -m rca tool"
$T question  bank-114                      # đề bài, danh sách máy và lý do
$T overview  bank-114                      # KPI nghiệp vụ từng phút, số log, số span
$T calls     bank-114                      # cặp máy gọi nhau: số lời gọi, thời lượng
$T exclusive bank-114                      # thời gian tự xử lý / mạng vào, theo máy
$T traces    bank-114                      # xếp hạng MicroRank
$T baro      bank-114 --top 15             # xếp hạng BARO
$T series    bank-114 Tomcat02 CPU         # metric của Tomcat02 có tên chứa "CPU"
$T series    bank-114 Tomcat02 ""          # liệt kê tên mọi metric của Tomcat02
$T logs      bank-114 Tomcat02 --grep GC   # log của Tomcat02 có chứa "GC"
$T onset     bank-114 Tomcat02             # Tomcat02 lệch từ lúc nào, trước hay sau máy gọi nó
```

Ghi giả thuyết và nộp đáp án. Ở đây `--run demo` là tên của lần chạy; kết quả nằm ở
`runs/demo/`:

```bash
$T hypothesis bank-114 add --run demo --component Tomcat02 --reason "high CPU usage" \
   --if-true "CPU Tomcat02 tăng trước các máy gọi nó" --if-false "CPU Tomcat02 không đổi"
$T hypothesis bank-114 add --run demo --component Mysql02 --reason "high disk I/O read usage" \
   --if-true "đọc đĩa Mysql02 tăng trước" --if-false "đọc đĩa Mysql02 không đổi"
$T hypothesis bank-114 test 1 --run demo --status supported --evidence "onset: CPU 27.6 -> 79.9 từ 16:22"
$T hypothesis bank-114 test 2 --run demo --status refuted --evidence "series sdc: DSKRead gần 0"
$T submit bank-114 --run demo --time "2021-03-09 16:22:00" --component Tomcat02 --reason "high CPU usage"
uv run python -m rca score runs/demo/predictions.jsonl --cases bank-114 --detail
```

Ba quy tắc về tham số hay gây nhầm:

- **Tham số chung đứng trước tên lệnh con.** `--data`, `--system`, `--runs` phải
  viết trước, ví dụ `python -m rca --system Telecom prepare`.
- **`--run` chỉ dùng cho `hypothesis` và `submit`.** Các công cụ còn lại báo lỗi nếu
  thêm `--run`.
- **Mã câu hỏi luôn đứng trước tên máy.** Viết `logs bank-114 Tomcat02`, không viết
  `logs Tomcat02 bank-114`.

### Cửa sổ khỏe

Thay mã câu hỏi bằng `normal-YYYY_MM_DD-HHMM` để mở một khoảng 30 phút không có câu
hỏi, ví dụ `normal-2021_03_06-0930`. Mọi công cụ chạy được trên đó, trừ `submit`.
Cửa sổ phải cách mọi câu hỏi ít nhất một giờ; nếu không, lệnh sẽ từ chối. Hồ sơ hệ
thống được viết từ các cửa sổ này.

## 6. Chạy agent

### 6.1 Chạy

Mở Claude Code ở thư mục gốc của repo. Đưa cho nó lời dặn dưới đây, sau khi thay ba
chỗ: danh sách câu hỏi, file hồ sơ, và tên run. Mỗi agent nên giải khoảng 10 câu.
Muốn chạy nhiều lô song song thì mở nhiều phiên hoặc nhiều subagent, nhưng tính đủ
RAM (mục 2).

```text
You are doing root-cause analysis (RCA) on incidents of a system. Solve each of these cases on your own,
one at a time, finishing one before starting the next: bank-001, bank-002, ..., bank-010.

## Rules (blind test, strict)
- Before the first case, read profiles/bank.md: a profile of this system written from healthy periods
  (no faults). Use it as background.
- Apart from that one file, gather evidence ONLY through `uv run python -m rca tool <name> ...`,
  run from the repository root.
- Do NOT read or grep anything under data/, runs/, README.md, SETUP.md, docs/, tests/ or any .py file.
  Do NOT run `python -m rca cases`, `score` or `baseline`, and do not run ad-hoc Python or SQL against
  the data. Those reveal the answers or skip the reasoning this test measures.
- Do not edit any files.

## How to work
Start every case with `uv run python -m rca tool question <case>`: it prints the query, the time window,
the components and reasons you must answer with, the available tools and how to submit. Follow the
hypothesis protocol the question describes.
The fault time is graded within ±60 seconds of when the root cause started. Component and reason must be
exact strings from the lists the question prints.
Always add `--run my-run` to every hypothesis and submit command. Submit one answer per failure
(`--append` for the second and later failures of a multi-failure query). Always submit time, component
and reason even if only some are asked. Budget: about 15–25 tool commands per case. Never leave a case
without a submission.

## Final report
For each case give: the submitted answer(s); the hypotheses you tested and why each was supported or
refuted (one line each); and your confidence (low/med/high).
```

Lời dặn viết bằng tiếng Anh vì đề bài, tên máy và tên lý do trong dữ liệu đều là
tiếng Anh.

### 6.2 Chấm điểm

```bash
uv run python -m rca score runs/my-run/predictions.jsonl --detail \
  --days 2021_03_04 2021_03_06 2021_03_07 2021_03_10 2021_03_12 2021_03_23 2021_03_24
```

- Nếu không truyền `--days`, lệnh `score` của Bank chỉ chấm các câu thuộc hai ngày
  9/3 và 25/3. Đó là 40 câu đã được dùng lúc phát triển.
- Bảy ngày trong lệnh trên chứa 96 câu còn lại, là tập dùng để đánh giá.
- Câu nào không có đáp án vẫn được tính vào mẫu số, với 0 điểm. Muốn chỉ chấm các
  câu đã giao cho agent, thêm `--cases bank-001 bank-002 ...`.
- Với Telecom, thêm `--system Telecom` trước `score`.

### 6.3 Soát lại phiên làm việc

Sau mỗi lần chạy, mở transcript của agent và kiểm tra rằng nó chỉ đọc file hồ sơ và
chỉ chạy lệnh `rca tool`. Nếu agent đã đọc `data/`, `runs/` hay mã nguồn, kết quả
của lần chạy đó không còn giá trị.

## 7. Các phương pháp so sánh

Các phương pháp này không dùng LLM. Mỗi lệnh ghi đáp án vào `runs/<tên>/`.

```bash
D="2021_03_04 2021_03_06 2021_03_07 2021_03_10 2021_03_12 2021_03_23 2021_03_24"
uv run python -m rca baseline oracle --days $D            # dùng chính đáp án: để kiểm tra bộ chấm
uv run python -m rca baseline random --seed 0 --days $D   # đoán ngẫu nhiên
uv run python -m rca baseline zscore --days $D            # thống kê z-score
uv run python -m rca baseline baro --days $D              # BARO tự đưa ra đáp án
uv run python -m rca score runs/oracle/predictions.jsonl runs/random/predictions.jsonl \
  runs/zscore-before/predictions.jsonl --days $D
```

Ở Telecom chỉ chạy được `oracle` và `random`. Hai phương pháp `zscore` và `baro` gán
lý do dựa vào tên metric của Bank.

## 8. Lỗi thường gặp

| Thông báo | Nguyên nhân và cách sửa |
|---|---|
| `unrecognized arguments: --run ...` | `--run` chỉ dùng cho `hypothesis` và `submit`. Bỏ nó ở các công cụ khác. |
| `no case Tomcat02` | Viết mã câu hỏi trước tên máy: `logs bank-114 Tomcat02`. |
| Báo thiếu `numpy` hoặc `baro` | Chạy `uv sync --group baro`. |
| `... lies within an hour of a query window` | Cửa sổ khỏe quá gần một câu hỏi. Chọn giờ khác, cách xa hơn. |
| `score` in ra `0/0` | Câu hỏi không thuộc các ngày mặc định. Thêm `--days ...` như ở mục 6.2. |
| Câu Telecom lúc 00:00 không có mức bình thường | Dữ liệu Telecom chỉ có từ 00:00 đến 06:00 mỗi ngày, nên không có "giờ trước" để so sánh. Đây là giới hạn của dữ liệu. |
| Máy hết RAM khi chạy nhiều agent | Giảm số agent chạy cùng lúc. Mỗi lệnh dùng tới khoảng 2 GB. |
