# CatFood 猫粮信息爬虫

面向猫粮分析 Demo 的 Python 爬虫，支持：

- https://www.zooplus.fi
- https://www.petenkoiratarvike.com

默认抓取示例清单中的 **6 个商品组，每站3个**。一个商品组可以包含多个包装规格。网站同名商品不会自动合并，避免把不同市场、配方或规格误当成同一产品。

## 1. 安装和运行

需要 Python 3.10 或以上版本。在解压后的 `CatFoodCrawler` 目录运行：

```bash
python -m venv .venv
# Linux / macOS
source .venv/bin/activate
# Windows PowerShell 使用：.venv\Scripts\Activate.ps1

python -m pip install -r requirements.txt
python crawler.py
```

运行结束后查看 `data/`。请求日志会显示 `GET`、`CACHE`、`OK`、`SKIP` 或 `STOP`。

## 2. 按自己的商品链接抓取（推荐）

编辑 `demo_urls.txt`，每行放一个商品详情页链接。也可新建 `my_urls.txt`：

```text
https://www.zooplus.fi/shop/kissat/kuivaruoka/royal_canin/erikoisravinto/142053
https://www.petenkoiratarvike.com/tuote/royal-canin-sterilised-P012378
```

```bash
python crawler.py --urls my_urls.txt --limit 10 --output my_data
```

`--limit` 是**每个网站**的成功商品组数量上限。清单不够、商品下架或页面解析失败时，程序会记录数量不足，不会伪造记录。每个详情页URL按商品组去重，查询参数中的包装选择不会让同一商品重复抓取；报价在 `variants` 中保留各SKU。

## 3. 自动发现商品链接

```bash
# Zooplus：猫粮干粮分类页，跟随页面中的“下一页”链接
python crawler.py --discover --site zooplus --limit 10 --pages 2

# Peten：分类页没有商品链接时，使用公开sitemap筛选候选商品
python crawler.py --discover --site peten --limit 10 --candidates 50

# 调整Peten的URL关键词筛选
python crawler.py --discover --site peten --keyword "kissan|cat-|kitten|applaws|sterilised" --limit 10
```

自动发现是有上限的采样，**不是全站完整目录**。Peten回退sitemap后可能发现干粮和湿粮，不再局限于原干粮分类。商品URL不总是包含猫粮类别，关键词可能漏掉产品；抓到候选页面后仍会检查面包屑，未确认属于猫粮的页面跳过。精确采集优先使用商品链接清单。分类页结构变化时不会调用隐藏接口或绕过访问限制。

## 4. 输出文件

| 文件 | 内容 |
|---|---|
| `products.json` | 主要数据文件：商品、SKU、报价、配料、营养原文及结构化观察值 |
| `products.csv` | 每个商品组一行，可用Excel打开；复杂字段以JSON字符串保留 |
| `offers.csv` | 每个规格的报价，保留币种、库存、会员/订阅价格条件 |
| `nutrients.csv` | 每条显式百分比营养指标一行，包含适用范围和原文 |
| `errors.json` | 跳过、阻塞、无法识别或无法访问的页面与原因 |
| `run_summary.json` | 成功数量、请求数、数量不足及整体运行状态 |

CSV使用UTF-8 BOM，并处理以公式符号开头的文本。JSON保留原始文本值。

关键字段：

```text
source_url / fetched_at        原始URL和实际下载时间（UTC）
source_html_sha256             本地HTML快照校验值
variants[].sku                包装规格SKU
variants[].offers[]            结构化报价及价格条件
ingredient_blocks[].raw       芬兰语配料原文
nutrition_blocks[].raw        营养原文，不仅限于百分比字段
nutrition_blocks[].scope      适用范围，必须和variant一起看
nutrition_blocks[].variant    对应SKU或页面营养表的规格标签
observations[].value_type     minimum / maximum / unspecified
warnings                      不一致、缺失及范围提示
review_status                 默认unreviewed，未人工审核
```

## 5. 数据使用方式

- **配料原文保留芬兰语**，不自动翻译，不猜测肉含量，不生成健康评分。
- **价格按SKU保留**。`priceSpecification` 可能包含划线价、会员价、订阅价或单位价；不能把页面结构化报价直接当成所有用户最终结算价。`AggregateOffer` 的最低/最高价不会转换为普通单价。
- **Zooplus营养表只代表当前HTML显示的规格**。程序读取被选中的选项；没有 `selected` 属性时记录首个选项及说明。不会把这张表自动复制到所有SKU。若需要可靠的特定SKU营养信息，应人工核对页面及包装。
- **Peten的配料和营养按SKU保存**，即使多个SKU的文本相同也不自动合并。
- **百分比数值必须在原文明确带 `%`**。mg/kg、kcal等仍保留在原文，当前版本不自动换算；`protein=37` 这类缺少单位的属性也不会擅自认定为37%。
- **上下限与实测值不混用**。明确的最低/最高限定词会保留；其余数值为 `unspecified`，不标成实测值。缺失水分不补成固定值，不自动算干物质或碳水。
- 不同营养块或规格出现不同数值时，保留所有观察值并提示检查适用范围。不同规格本来就可能有不同配方，提示不等于证明数据错误。
- `size_raw` 是网站结构化规格字段，可能是范围（如 `< 1,4 kg`）。同时查看SKU名称，不据此直接推算每公斤价格。
- 商品图片仅保存URL，不批量下载图片。商用展示前确认所用资料和图片的许可范围。

## 6. 请求和缓存

默认同一网站请求间隔5秒，参数最低3秒；若robots要求更慢则使用更长间隔。单线程运行，没有代理轮换或浏览器指纹模拟。

```bash
python crawler.py --delay 8 --limit 3
python crawler.py --site zooplus --cache-hours 0 --limit 1
python crawler.py --user-agent "CatFoodDemoBot/1.0 (contact: your-email@example.com)"
```

请把示例邮箱换成你自己的真实联系方式。

- 每次运行先检查 `robots.txt`，支持 `*` 通配符、末尾 `$` 和更具体的User-agent组。只抓取这两个零售商的HTTPS URL。
- robots返回404视为没有公布规则；无法读取或返回异常页面时停止该网站。
- HTTP 401/403/429 或验证页面会停止该网站，不自动反复重试。
- 默认缓存24小时；缓存复用时保留原下载时间，不把旧价格标成今天的新价格。
- 原始HTML缓存在 `.cache/`，用于本地排查。快照可能含有页面其他内容，不直接发布缓存目录。
- `--max-requests` 默认80，包含robots、分类页、sitemap和商品请求。
- 默认输出文件会覆盖同目录上一轮结果；要保留历史，请使用不同 `--output` 目录。
- 退出码：0=达到目标且没有错误，2=部分成功，1=没有成功商品。详情看摘要和错误文件。

## 7. 验证与维护

```bash
python -m unittest discover -s tests -v
```

包含营养单位及上下限、商品规格、数据冲突、猫粮类别判断、链接去重、robots规则、缓存和CSV公式文本等验证。

网站适配主要在 `extract_zooplus()`、`schema_nodes()`、`parse_product()` 和 `discover()`。缺少结构化Product数据会明确报错，而不会生成“成功但内容为空”的商品。

适配依据：2026-09-24直接读取的公开HTML、JSON-LD、robots和sitemap。页面可能改变；本工具不承诺全站、全部规格或全部营养指标完整覆盖。真实运行结果和已验证范围见 `VALIDATION.md`。
