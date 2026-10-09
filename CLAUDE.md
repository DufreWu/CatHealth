# PawBase 首版开发约定

入口和范围以 README.md 为准。已有 crawler.py 格式不能静默破坏；新管线在 pipeline.py 适配。

- 不打分、不排名，不把分档表述成安全或疾病适用结论。
- 缺失为 null；禁止估算水分。上下限标签和冲突值不能当作精确值。
- 不混合 SKU 适用范围；新数据源不得自动借用其他渠道缺失字段。
- 基准/情境提示在 config/benchmarks.json，声明词表在 config/declarations.json。
- 每年 9–10 月核验新版 FEDIAF；记录真实核验日期和引用；需要变更时升级框架版本并运行 reanalyze.py。
- 不采集 Musti；不绕过访问限制；robots 变化及 403/429 中止；单线程、随机 2–5 秒或更慢。
- 原数据与虚构演示数据分库。测试不要修改 data/pawbase.db。
- 前端不含医疗适用性推断，原型文献尚待核验；上线前由产品确认。
- 运行 python -m unittest discover -s tests -v；前端修改检查 tests/e2e.cjs。
