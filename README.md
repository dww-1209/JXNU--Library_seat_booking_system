# 砚席 · 江西师范大学图书馆自动选座助手

专为江西师范大学（瑶湖图书馆、瑶湖西区自习室）超星学习通座位预约系统打造的独立 Windows 单文件桌面应用。无需安装 Python、Node.js 或浏览器插件，也无需解压任何附属文件夹——下载单个 `YanxiSeatReserver.exe` 放到桌面直接双击运行，像设定闹钟一样选好教室与顺位座位、开启每日自动抢座，随后直接关闭窗口即可由系统在后台静默完成每晚 22:00 的次日选座与核验。

## 核心亮点

- **真正单文件 `.exe` 桌面端**：仅需一个 `YanxiSeatReserver.exe`，无需解压 `_internal` 等附属目录，且不会在桌面或 `.exe` 同级目录生成任何杂乱文件。
- **全馆平面图与矩阵可视化选座**：自动同步全馆阅览室与自习室（支持带建筑平面底图的精确坐标选座，以及无底图自习室的矩阵网格选座），直接针对次日目标座位进行配置。
- **多座位优先级拖拽队列**：点击座位按 `1..N` 顺位加入备选队列，支持拖拽调整优先级与批量区间连选；抢座时严格按顺位提交，遇到已被占用的席位自动剔除并立即顺延至下一顺位。
- **闹钟式“每日自动抢座”托管**：一键注册独立的 Windows 每日静默计划任务，每晚定时唤醒并在后台静默完成预热、时钟校准、抢座与核验；提供 **“仅跳过今晚”** 开关（单晚静默跳过，明晚自动恢复）与 **“取消自动抢座”** 一键停用。
- **RTT 服务器时钟校准与主动核验**：预热阶段自动对齐学习通服务器时钟，抢座结束后主动查询服务器实际生效记录，随时支持点击“检测服务器预约状态”确认真实结果。
- **全链路本地诊断日志**：底部可展开实时诊断控制台，自动记录毫秒级请求轨迹并将脱敏日志持久化至系统用户本地数据目录。

## 下载与使用（单文件 `.exe` 桌面程序）

> 适用系统：Windows 10 / 11（64 位），系统自带 Microsoft Edge 即可运行，无需安装 Python、`uv` 或 Node.js。

### 获取程序

1. 前往本仓库 [Releases 页面](https://github.com/dww-1209/JXNU--Library_seat_booking_system/releases) 直接下载单个可执行文件 **`YanxiSeatReserver.exe`**。
2. 将 `YanxiSeatReserver.exe` 放在桌面或任意固定目录（开启每日后台任务后请勿随意移动 `.exe` 位置），**无需解压，直接双击启动**。

### 四步完成配置

1. **登录超星账号**：点击左上角账号区域，输入学习通手机号与密码完成登录（凭据仅保存在本机用户数据目录）。
2. **选择教室与备选顺位**：在左侧切换瑶湖图书馆或西区自习室，在中间平面图上依次点击目标座位形成 `1, 2, 3...` 优先级队列，可在右侧拖拽调整顺位或设置使用时段（如 `08:00 - 21:00`）。
3. **开启每日自动抢座**：在右侧“每日自动抢座”卡片中确认后台唤醒时间（默认 `21:59`），点击 **“开启每日自动抢座”**。
4. **直接关闭窗口**：看到“已托管后台”提示后即可直接关闭应用窗口；每晚系统会自动在后台静默完成次日抢座并在成功后发出提示音。
   - **临时休息一晚**：点击 **“仅跳过今晚”**，当晚任务将静默跳过并在明晚自动恢复。
   - **暂停每日任务**：点击 **“取消自动抢座”** 即可移除后台计划任务。
   - **随时查验与排障**：可随时点击 **“检测服务器预约状态”** 核对真实预约记录，或展开底部诊断控制台查看实时运行日志。

## 隐私与安全说明

- **完全本地运行，桌面零残留**：个人账号配置、会话状态、底图缓存与诊断日志统一存放在 Windows 标准用户本地数据目录 **`%LOCALAPPDATA%\YanxiSeatReserver\`**（包含 `config.json`、`storage_state.json`、`logs/`、`cache/`），不会在桌面或 `.exe` 旁产生任何文件，绝不上传至任何第三方服务器，亦不会被提交至 GitHub 仓库。
- **日志自动脱敏**：写入磁盘与展示在诊断控制台中的日志会自动对手机号、密码与 Cookie 进行掩码处理。
- **其他高校适配**：本应用默认配置江西师范大学机构编码（`deptIdEnc`）；其他使用超星学习通选座系统的高校用户，可在登录弹窗中替换本校的 `deptIdEnc` 即可自动加载本校全馆教室与平面图。

## 分层架构说明

项目采用端口与适配器（Ports & Adapters）分层解耦设计，上游领域模型与核心业务流程完全不依赖超星接口细节、Playwright、FastAPI 或操作系统实现：

```text
.
├── domain/              # 领域层：Pydantic v2 数据模型 (models.py)、抽象端口 (ports.py) 与运行路径解析 (paths.py)
├── services/            # 上游业务层：配置管理、阅览室与座位布局、预约状态机、LogBus 日志总线
├── adapters/            # 下游适配层：超星 HTTP/Playwright 适配器、JSON 配置存储、蜂鸣提醒、Windows 计划任务
├── ui/                  # 本地服务层：FastAPI REST + SSE 实时推送与静态前端托管 (server.py)
├── frontend/            # 桌面前端：React 19 + TypeScript + Vite + Tailwind CSS v4 + Motion
├── tests/               # 单元测试：覆盖优先级顺延、时钟校准、核验恢复、skipDate 与冻结路径解析
├── main.py              # 桌面端与后台任务统一入口
├── yanxi.spec           # PyInstaller 单文件 (onefile) 无控制台独立打包配置
├── pyproject.toml       # Python 依赖声明与工具配置
├── uv.lock              # 依赖锁文件
└── config.example.json  # 配置文件示例模板
```

<details>
<summary><strong>开发者自行打包单文件 <code>.exe</code>（点击展开）</strong></summary>

如需从源码重新构建 Windows 单文件 `.exe`，需要准备 [uv](https://docs.astral.sh/uv/getting-started/installation/) 与 Node.js 20+：

```powershell
# 1. 安装 Python 依赖（含 PyInstaller）
uv sync

# 2. 构建桌面端前端静态资源
cd frontend
npm install
npm run build
cd ..

# 3. 使用内置 yanxi.spec 打包为单文件无控制台 .exe
uv run pyinstaller --noconfirm yanxi.spec
```

打包完成后生成单个独立可执行文件 **`dist/YanxiSeatReserver.exe`**，可直接上传至 GitHub Releases 分发。

</details>
