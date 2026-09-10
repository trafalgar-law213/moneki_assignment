# POKE ONE 连锁餐饮经营看板 + AI 数据问答

[![CI](https://github.com/trafalgar-law213/moneki_assignment/actions/workflows/ci.yml/badge.svg)](https://github.com/trafalgar-law213/moneki_assignment/actions/workflows/ci.yml)

5 家门店脱敏 POS 数据（2026-05-01 ~ 2026-07-31，清洗后 11,815 条流水）→ **经营看板 + AI 自然语言问答**。

核心硬指标：**AI 回答中的每个数字都来自真实数据库查询，与看板接口对账一致**——由「唯一取数层」架构保证（看板 API 与 AI 工具共用同一套查询函数），并有 mock/live 双层自动化测试守护。

## 四步跑起来

```bash
# 1. 数据库（PostgreSQL 16，本地开发用 Docker 一条命令）
docker run -d --name pokeone-pg -e POSTGRES_PASSWORD=pokeone_dev -e POSTGRES_DB=pokeone \
  -p 5432:5432 postgres:16

# 2. 后端（Python 3.10+）
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/download_model.py # 可选：本地 embedding 模型（~95MB，跨会话语义检索用；不下载则该功能降级）
cp .env.example .env             # 填入 DEEPSEEK_API_KEY（AI 问答需要；不填则只有看板可用）
uvicorn app.main:app --port 8000 # 启动时若库未初始化则自动清洗 data/*.csv 建库（幂等），并托管前端构建产物

# 3. 前端（Node 18+）
cd ../frontend
npm install
npm run build                    # 产物 dist/ 由后端直接托管

# 4. 打开浏览器
# http://localhost:8000
```

> 开发模式：后端跑 8000，前端 `npm run dev`（vite 已代理 `/api` → 8000）。
> 依赖安装也可用 uv：`uv venv && uv pip install -r requirements.txt`。

## Docker 一键部署（生产）

```bash
docker compose up -d --build
# 访问 http://服务器IP:8000
```

- 两容器：`db`（postgres:16，数据持久化在 pgdata 卷）+ `pokeone`（应用镜像，`depends_on` 带健康检查、等数据库就绪才启动）
- 前置：`backend/.env` 填好 `DEEPSEEK_API_KEY`（gitignore 文件，运行时经 compose `env_file` 注入，**绝不进镜像**）
- 单镜像多阶段构建：node 阶段构建前端 → python 阶段运行 FastAPI 并托管 dist；容器启动时若库未初始化，自动清洗 `data/*.csv` 建库（幂等）
- 生产 PG 密码：`POSTGRES_PASSWORD=强密码 docker compose up -d`（默认值仅本地开发用）
- 公网访问保护（可选）：在 `backend/.env` 加一行 `ACCESS_PASSWORD=你的口令` 并重建容器，即开启登录页 + AI 接口双层限流（单 IP 每分钟 5 次 / 全站每天 200 次，`RATE_PER_MIN` / `RATE_GLOBAL_PER_DAY` 可调）；**不设置则保护整体关闭**（本地开发默认如此，自动化测试不受影响）

## 功能

**看板**（第一关 + 加分）
- 日期区间筛选（本月/上月/近 30 天等快捷项 + 自定义 + 门店下拉）
- 营业额趋势 + 客单价双轴图、Top10 商品、门店对比、品类对比、KPI 卡片（含环比）
- 异常销售预警（加分）：**同星期对比** z-score（周末和周末比、工作日和工作日比，排除"周末天然火爆"的周期性误报），以文字给出异常日与原因（如"05-05（周二）Super Tetsudo ▼偏低 ¥335，较同周二日均 -57.4%"）
- 手写深色主题 UI（无组件库，加分项）

**AI 问答**（第二关 + 第三关全部进阶）
- 自然语言问数据 → LLM 出工具参数 → 后端白名单 SQL 真查库 → 流式回答（思考过程可见）
- 追问上下文（"那五月呢？"）；查不到如实说"数据里没有"，绝不编造
- 图表联动：AI 查询的区间自动同步到看板；「✨ 让 AI 给经营建议」一键调用
- **跨会话记忆**：每次问答自动向量化入库（pgvector），右侧「历史问答」面板可语义检索——换种问法也能找回

## 架构（依赖单向，模块解耦）

```
pipeline（清洗，可独立运行）──写──▶ PostgreSQL ◀──读── db（连接层）
                                        ▲
                      query（★唯一取数层：看板与 AI 共用，数字一致性由架构保证）
                        ▲                    ▲
              api/dashboard（看板接口）   ai/tools（AI 工具注册表）
                        ▲                    ▲
                                     api/chat ◀── ai/client（DeepSeek V4 Pro，SSE 流式）
```

- 加/减功能只改对应模块：新 AI 工具 = 加一个函数 + 注册，不动聊天链路
- AI 回答数字 = 看板数字，**不需要人工对账，架构上就一致**

## 测试

```bash
# 前置：本地 PostgreSQL 已在运行（见「四步跑起来」第 1 步；测试会自动建临时库，跑完删除）
cd backend && .venv/Scripts/python.exe -m pytest tests/ -q   # Windows
cd backend && .venv/bin/pytest tests/ -q                     # Linux/macOS
```

> CI：push / PR 时由 GitHub Actions 自动跑全部测试（`.github/workflows/ci.yml`；live 测试无 key 自动跳过）。

65 个测试分五层（live 测试无 key 自动 skip）：
| 层 | 文件 | 守护什么 |
|---|---|---|
| 清洗 | test_pipeline.py ×27 | 8 类脏数据规则逐一断言 + 幂等重跑 |
| 看板 | test_dashboard.py ×15 | 接口数字与手工期望/直接 SQL 对账、异常算法定向测试、非法参数 400 |
| AI mock | test_ai_consistency.py ×8 | 「回答中每个数字 ⊆ 工具结果数字集」+ 兜底不编造 + 追问历史回传（mock LLM、工具真执行） |
| AI live | test_ai_live.py ×6 | 真 key 打真模型，三样例问题数字与库内锚点对账（约 1~3 分钟） |
| 安全 | test_security.py ×9 | 口令登录/HMAC cookie 会话防伪造/401 拦截/聊天接口双层限流（未设口令时保护关闭） |

## 技术选型理由

| 选择 | 理由 |
|---|---|
| FastAPI + PostgreSQL | v1 原型用 SQLite（零运维、单文件）；v2 迁 PostgreSQL——pgvector 向量能力 + 生产化（多实例/并发/备份），见「数据库演进」 |
| React 18(Vite) + ECharts | 业界主流栈；ECharts 双轴图开箱即用 |
| 手写 CSS 深色主题 | 纯手写、不套组件库，审美与视觉工程完整可控 |
| 本地 embedding（bge-small-zh + ONNX） | 中文语义质量好、CPU 推理快；自实现推理免 torch 重依赖；模型经 ModelScope 获取适配国内网络 |
| DeepSeek V4 Pro + function calling | 结构化工具调用：LLM 只出参数，SQL 由后端白名单拼装+参数化绑定，防注入、可测试 |
| SSE 流式 | 首字秒回 + 思考过程透明，体验好且实现稳 |
| 单容器部署（FastAPI 托管 dist） | 3 小时窗口内最少翻车点；README 已写清取舍 |

## 数据库演进（v2：SQLite → PostgreSQL）

| 阶段 | 选型 | 理由 |
|---|---|---|
| v1（原型） | SQLite | 1.2 万行单机 demo：零运维、单文件、读写够快——原型期的最优解 |
| v2（当前） | PostgreSQL | ① pgvector 向量能力（RAG 的基础设施，SQLite 没有）；② 生产化：多实例部署共享数据、并发写入、备份与高可用 |

迁移是真实的工程动作，实际替换的内容：连接层（sqlite3 → psycopg）、值占位符方言（`?` → `%s`）、`ROUND(double, 2)` 经 `NUMERIC` 中转（PG 无此重载）。**PG 的严格性还暴露了两处被 SQLite 宽容掩盖的写法**：GROUP BY 必须覆盖 SELECT 的全部非聚合列；外键默认强制（定向测试因此补出一处数据完整性缺口）。迁移的安全网是既有测试：65 个全绿即迁移成功的证明。

**pgvector 已落地**：问答历史表（qa_history）用 bge-small-zh 向量做语义检索，见下节。

## 跨会话记忆（语义检索）

会话内记忆（追问"那五月呢"）由前端回传历史实现；**跨会话**由服务端补：每次问答自动向量化入库（pgvector），右侧「历史问答」面板可按语义检索——换种问法也能命中（实测同义提问相似度 0.99，无关提问 0.25）。

- 向量方案：bge-small-zh-v1.5，ONNX 自实现推理（onnxruntime + tokenizers，不引入 torch 重依赖）；模型经 ModelScope 获取（适配国内网络），见 `backend/scripts/download_model.py`
- **设计取舍：历史答案不进入 AI 的数字链路**——数字必须来自「本次查询的工具结果」；历史仅服务用户自查与展示（让 AI 直接引用历史答案会绕过"数字来自本次查询"的对账口径）
- 降级策略：模型缺失时该功能静默关闭（接口返回 503），问答主链路不受影响（有测试守护）

## 数据策略（四层隔离，数据内容不进 AI 上下文）

1. **架构层**：LLM 只能经白名单工具拿数据，工具层只返回聚合结果，全链路不存在返回原始行数据的工具
2. **SQL 层**：白名单拼装 + 参数化绑定，杜绝 SQL 注入
3. **提示词层**：系统提示词只注入元信息（日期边界/门店名/品类），不含行级数据
4. **开发侧**：开发过程不通读大 CSV，只读前 3 行验格式，一切处理走脚本看聚合数字

清洗可审计：重复行去重（78）、坏日期/坏金额/脏外键等进入隔离表（238 行，逐行记录原因+原始行），审计等式 `12,131 = 11,815 + 78 + 238`，报告见 `backend/data/quality_report.json`。

## 目录

```
├── data/            # 数据副本（清洗只碰副本；原始数据夹只读）
├── backend/         # FastAPI：pipeline 清洗 / query 唯一取数层 / api 看板+聊天 / ai 工具+客户端 / tests
├── frontend/        # React+Vite：组件各自取数，事件总线联动（AI 区间 → 看板跳转）
├── .github/         # GitHub Actions：push/PR 自动跑测试（含 PG 服务容器）
└── AI_USAGE.md      # AI 使用说明（AI 协作分工与开发规范）
```
