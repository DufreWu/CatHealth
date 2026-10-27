# CatHealth / PawBase Food Guide — 首版前后端联调

在原始 CatHealth 爬虫基础上增加可运行的猫粮指南。Python 3.10+，SQLite，原生 HTML/CSS/JavaScript；前后端同源，不需要 Node 构建、API 密钥或付费服务。交付包中没有交接文档提到的 React/Claude/病例库源码，因此首版新建轻量界面和数据层，保留原始爬虫及原始抓取数据。

## 1. 最快启动

解压后进入包含 `app.py` 的 CatHealth 目录：

```bash
python app.py --demo
```

浏览器打开 **http://127.0.0.1:8000**。虚构演示数据独立写入 `data/demo.db`，含干粮、湿粮、幼猫、老年猫、缺失水分、异常标签和处方粮。处方粮默认隐藏。

真实历史数据模式（先 Ctrl+C 停止演示服务）：

```bash
python app.py
```

启动时自动从 `data/bootstrap.json` 初始化 `data/pawbase.db`。这些是原代码包 2026-09-24 抓取结果及其缓存中提取的产品正文，**不是本次重新联网抓取的实时数据**。两种模式不混用数据库。

如果端口已占用：`python app.py --demo --port 8002`。

Windows 可将命令里的 `python` 换成 `py`。网页启动及历史数据导入只使用 Python 标准库；只有实时爬虫/旧 HTML 缓存迁移需要安装 `requirements.txt`。

## 2. 已实现

- 爬虫结果 → 声明精筛 → 单位规范化、数值校验 → SQLite → 确定性营养计算 → HTTP API → 前端。
- 全价粮声明词表；补充粮、声明缺失和类型不明记录进入排除清单，不猜测归类。
- 逗号小数、%、g/kg、g/100g、mg/kg；保留原文、单位和上下限证据。
- 五项合计 >100%、冲突或区间异常进入复核，阻止分析。缺失值保持 null。
- DM、磷/钙/镁 DM、钙磷比；按 FEDIAF 2025 引用的 NRC 2006 方程计算 ME（五项声明值齐全时），磷/钙/钠/蛋白折算为每 1000 kcal ME。碳水不展示，仅内部用于 ME。
- 框架 v2.2（依据 assets/project_summary.pdf）：四级判定（超过法定上限/超出营养上限/低于推荐值/达到最低推荐值以上 + 未判定），未舍入值比较，目前仅蛋白质有参照值；磷 >3.6（信息级）/>5.0（警示级）g/1000 kcal ME、钙磷比 <1:1、钠 >3.75 g/1000 kcal ME 提示，提示不改变判定。
- 牛磺酸声明仅表示提取文本中是否出现相关声明，不代表完整营养充足性。
- 基准独立配置、幼猫蛋白最低值、阶段说明、中性分档、同类同阶段 p25–p75 分布带。没有总分、星级或排名。
- 搜索、品牌/类型/生命阶段/无谷筛选，处方粮默认隐藏，分页、详情、2–3 个商品对比、关注镜头。
- 查看标签原文、适用规格、抓取时产品正文快照、价格来源与日期。
- 按 source + 产品 ID + 营养适用范围去重；配方变化自动重算，价格更新；相同范围不会重复入库。
- 抓取成功率 <80% 或蛋白/水分/参考价 null 率上升 >20 个百分点，整批隔离、不修改主库；界面显示运行摘要、排除记录和复核清单。
- 原爬虫增加 robots 规则指纹变更停止、随机 2–5 秒间隔、显式 PawBase 联系方式，以及 403/429/访问限制整轮停止。robots 更慢时遵守更慢间隔。原始商品导出仍可独立使用。

## 3. 数据事实与首版边界

原包只有 6 个商品组，不是交接说明中的 27 款完整分析结果。保守迁移结果为：

- Zooplus 3 个商品组，3 条营养范围记录。其中 Sterilised 37 的纤维原文 6.2% 与表格 6.1% 冲突，进入复核；Applaws 缺水分，不计算 DM；Indoor 可计算 DM。
- Peten Sterilised 的 4 个 SKU 分别保留，均缺水分，不计算 DM。
- Peten Applaws 未检出配置中的全价粮声明；Peten Indoor 无法从已提取字段确认干/湿类型。两者进入排除清单，不能据此断言商品本身不是全价粮。
- 共 7 条营养范围记录，来自 4 个商品组。页面计数为记录数，不是去重的配方总量。跨源/跨 SKU 尚未合并，同配方多个 SKU 会影响分布样本，因此目前仅供联调，不代表市场分布。
- 价格条件不清晰时保留 null。当前 Zooplus JSON-LD 含促销/会员/单位价，首版不据此生成标准参考价；Peten 的无条件 Offer 可展示。
- 网站没有用于外网部署的身份认证。默认只绑定本机；数据复核仅为只读本地工具。

以下留待后续：全站全量与分页覆盖、Playwright 动态渲染采集、跨源人工合并、连续两轮缺失下架、定时调度、AI 蛋白来源/诱食剂结构分析、€150 AI 预算守卫、病例库、知识库、线上账号与部署。没有实现的功能不会显示成已完成。

**基准边界按 PRD 实现，引用文献尚未独立核验。** `/methodology` 明确显示“待核验”，不伪造核查日期或链接；正式对外发布前由产品侧逐条确认。原文中“天然肉等估算比例”和疾病适用标签未实现。

## 4. 采集、导入和复核

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

# 真实邮箱替换下面的占位符；执行前核对目标站点当前规则。
# --limit 是每站最多抓取数量上限，不是“必须抓到多少条”。
# 若使用 --urls，实际可抓总量由输入文件中的 URL 数量决定。
python crawler.py --site zooplus --discover --pages 0 --candidates 0 --limit 0 \
  --max-requests 8000 --cache-hours 0 \
  --user-agent "PawBaseBot/0.1 (contact: YOUR_REAL_EMAIL)" \
  --output data/run_zooplus --database data/pawbase.db

python crawler.py --site peten --discover --candidates 0 --limit 0 \
  --max-requests 3000 --cache-hours 0 \
  --user-agent "PawBaseBot/0.1 (contact: YOUR_REAL_EMAIL)" \
  --output data/run_peten --database data/pawbase.db
```

`--pages/--candidates/--limit` 为 0 表示不设上限；`--max-requests` 仍是硬上限，需大于分类页数 + 商品数（约每请求 2–5 秒，数千商品需数小时，建议夜间按站点分别运行）。中断或有失败时，加 `--resume` 和 `--cache-hours 24` 重跑同一命令：保留 `--output` 中已有的 `products.json`，只抓取缺失的商品，合并后整体导入数据库（已有记录幂等更新）。

`run_summary.json` 的 `discovery` 记录每个分类的翻页数、发现链接数、网站自报总数（`reported_total`）和是否翻到最后一页（`complete`），用于核对覆盖率。

`YOUR_REAL_EMAIL` 必须换成实际邮箱，否则程序拒绝运行。`--limit` 是上限，不是保证值；当 `--urls` 指向有限列表时，最终抓取数等于实际可用 URL 数量，最多不超过该站点上限。并发始终为 1。`--delay >5` 可要求更慢；2–5 范围内采用随机间隔。建议人工在 Europe/Helsinki 时区 22:00–06:00 运行；没有自动调度。

程序每轮重新读取 robots。第一次建立本地规则指纹；规则变化时中止，人工核对后删除相应 `.cache/robots_<host>.sha256` 才能建立新基线。遇到 403/429 不继续另一站，不重试。首次规则基线并不替代条款许可确认。若 `--urls` 文件里只有 6 个 URL，即使写 `--limit 300`，最终也不会被伪造成 300 条；汇总中 `shortfall` 仅在“实际可抓取的上限未填满”时出现。

只导入旧爬虫导出的 JSON：

```bash
python manage.py import data/new_run/products.json --errors data/new_run/errors.json
python manage.py review
python manage.py export data/analyzed_export.json
```

如果输入是早期无正文的输出，有原始 HTML 缓存可用时：

```bash
python manage.py import data/products.json --cache .cache
```

没有正文或完整声明的旧记录会被排除，不能省略声明要求。原始完整 HTML 缓存不包含在新版交付包；已迁移的产品正文在 `bootstrap.json`。导入退出码 2 表示触发隔离。单源批次与混合源批次分别比较历史空值率；建议一直按站点分别运行，确保相同采样范围，避免小批次之间误报。

## 5. 配置与重分析

`config/benchmarks.json` 控制名称、边界、参考线、依据、情境提示。后端每次请求读取配置，**修改边界后刷新页面即生效**；前端无硬编码分档阈值。边界约定遵循 PRD：40% 蛋白仍属 moderate；1.4% 磷和30%碳水仍属 moderate。

```bash
python reanalyze.py --framework 2.1 --outdated-only
# 指定库
python reanalyze.py --db data/demo.db --framework 2.1
```

这是确定性计算重跑，无 Anthropic 调用，也不产生 API 花费。`--outdated-only` 重算版本与目标不同的记录。目标必须匹配当前配置版本。调整计算逻辑时必须升级版本；只调整展示边界无需重算数值。

年度维护：每年 9–10 月检查 FEDIAF 新版，核对引用和边界，有变化则更新配置与 framework_version，执行重分析；争议添加说明，不擅自改成安全结论。

## 6. API 与代码入口

| 文件/接口 | 用途 |
|---|---|
| `crawler.py` | 保留现有两站采样采集，增加访问约束与入库选项 |
| `pipeline.py` | 原数据格式适配、声明筛选、规格隔离、参考价判定 |
| `nutrition.py` | 单位转换、校验、营养换算、分档与情境提示 |
| `database.py` | SQLite、原子入库、隔离、去重、分布统计 |
| `app.py` | 本机 HTTP API 与静态文件服务 |
| `web/` | 响应式暗色前端，无 CDN 依赖 |
| `GET /api/products?q=&brand=&type=&stage=&prescription=true` | 搜索/筛选/分页 |
| `GET /api/products/{id}` | 详情、原文、快照与参考价 |
| `GET /api/compare?ids=id1,id2` | 2–3 个商品对比 |
| `GET /api/benchmarks` | 当前配置 |
| `GET /api/stats` | 同类型同阶段分布 |
| `GET /api/review` | 只读复核、排除、隔离与导入日志 |
| `GET /api/health` | 运行模式及记录数 |

当前数据量小，统计由后端请求时聚合；尚未实现大规模物化缓存。前端不自行算分档或统计。全部写操作在 CLI，不提供无认证公网写入接口。

## 7. 验证

```bash
python -m unittest discover -s tests -v
```

测试覆盖原有爬虫、单位/边界/缺失值、数据校验、重复入库、隔离原子性、版本重算、配置刷新、HTTP 搜索筛选与对比。

可选真实浏览器检查：

```bash
npm install --no-save playwright
npx playwright install chromium
node tests/e2e.cjs
```

浏览器脚本启动临时演示服务、运行桌面/移动页面测试并生成 `docs/desktop.png` 与 `docs/mobile.png`。详细执行结果见 `docs/DELIVERY.md`。
