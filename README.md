# POKE ONE 连锁餐饮经营看板 + AI 数据问答

求职实操作业：5 家门店脱敏 POS 数据（2026-05-01 ~ 2026-07-31，清洗后 11,815 条流水）→ **经营看板 + AI 自然语言问答**。

核心硬指标：**AI 回答中的每个数字都来自真实数据库查询，与看板接口对账一致**——由「唯一取数层」架构保证（看板 API 与 AI 工具共用同一套查询函数），并有 mock/live 双层自动化测试守护。

## 三步跑起来

```bash
# 1. 后端（Python 3.10+）
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # 填入 DEEPSEEK_API_KEY（AI 问答需要；不填则只有看板可用）
uvicorn app.main:app --port 8000 # 启动时自动清洗 data/*.csv 建库（幂等），并托管前端构建产物

# 2. 前端（Node 18+）
cd ../frontend
npm install
npm run build                    # 产物 dist/ 由后端直接托管

# 3. 打开浏览器
# http://localhost:8000
```

> 开发模式：后端跑 8000，前端 `npm run dev`（vite 已代理 `/api` → 8000）。
> 依赖安装也可用 uv：`uv venv && uv pip install -r requirements.txt`。

## Docker 一键部署（生产）

```bash
docker compose up -d --build
# 访问 http://服务器IP:8000
```

- 前置：`backend/.env` 填好 `DEEPSEEK_API_KEY`（gitignore 文件，运行时经 compose `env_file` 注入，**绝不进镜像**）
- 单镜像多阶段构建：node 阶段构建前端 → python 阶段运行 FastAPI 并托管 dist；容器启动时若数据库缺失，自动清洗 `data/*.csv` 建库（幂等）
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

## 架构（依赖单向，模块解耦）

```
pipeline（清洗，可独立运行）──写──▶ SQLite ◀──读── db（连接层）
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
cd backend && .venv/Scripts/python.exe -m pytest tests/ -q   # Windows
cd backend && .venv/bin/pytest tests/ -q                     # Linux/macOS
```

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
| FastAPI + SQLite(WAL) | 1.2 万行数据量级 SQLite 绰绰有余；零部署依赖；WAL 支撑并发读 |
| React 18(Vite) + ECharts | 题目点名的主流栈；ECharts 双轴图开箱即用 |
| 手写 CSS 深色主题 | 题目要求"有审美、不要组件库套壳" |
| DeepSeek V4 Pro + function calling | 结构化工具调用：LLM 只出参数，SQL 由后端白名单拼装+参数化绑定，防注入、可测试 |
| SSE 流式 | 首字秒回 + 思考过程透明，体验好且实现稳 |
| 单容器部署（FastAPI 托管 dist） | 3 小时窗口内最少翻车点；README 已写清取舍 |

## 数据策略（四层隔离，数据内容不进 AI 上下文）

1. **架构层**：LLM 只能经白名单工具拿数据，工具层只返回聚合结果，全链路不存在返回原始行数据的工具
2. **SQL 层**：白名单拼装 + 参数化绑定，杜绝 SQL 注入
3. **提示词层**：系统提示词只注入元信息（日期边界/门店名/品类），不含行级数据
4. **开发侧**：开发过程不通读大 CSV，只读前 3 行验格式，一切处理走脚本看聚合数字

清洗可审计：重复行去重（78）、坏日期/坏金额/脏外键等进入隔离表（238 行，逐行记录原因+原始行），审计等式 `12,131 = 11,815 + 78 + 238`，报告见 `backend/data/quality_report.json`。

## 目录

```
├── data/            # 数据副本（清洗只碰副本；题目夹只读）
├── backend/         # FastAPI：pipeline 清洗 / query 唯一取数层 / api 看板+聊天 / ai 工具+客户端 / tests
├── frontend/        # React+Vite：组件各自取数，事件总线联动（AI 区间 → 看板跳转）
├── AI_USAGE.md      # AI 使用说明（题目必交）
└── DEMO.md          # 演示脚本（题目必交）
```
