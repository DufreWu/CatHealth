#!/usr/bin/env python3
"""Small, rate-limited public cat-food crawler. Python 3.10+.

Outputs retain source text, offer conditions and nutrition scope. No health
scores, inferred moisture, browser automation, login or hidden API calls.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import re
import random
import sys
import time
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

LOG = logging.getLogger("catfood")
VERSION = "1.0.0"
HOSTS = {"www.zooplus.fi", "zooplus.fi", "www.petenkoiratarvike.com", "petenkoiratarvike.com"}
# 每站按顺序遍历的分类页；Zooplus 干粮与湿粮是两个独立分类。
CATEGORIES = {"zooplus": ["https://www.zooplus.fi/shop/kissat/kuivaruoka",
                          "https://www.zooplus.fi/shop/kissat/markaruoka"],
              "peten": ["https://www.petenkoiratarvike.com/kategoria/kissat/kissanruoka/kissan-kuivaruoka-211"]}
CORE = {
    "protein": r"raakaproteiini|raakavalkuainen|crude protein",
    "fat": r"raakarasva|rasvapitoisuus|crude fat",
    "fiber": r"raakakuitu|crude fib(?:er|re)",
    "ash": r"raakatuhka|tuhka|crude ash",
    "moisture": r"kosteus|moisture",
    "calcium": r"kalsium|calcium",
    "phosphorus": r"fosfori|phosphorus",
    "magnesium": r"magnesium",
    "taurine": r"tauriini|taurine",
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def text_html(value):
    return BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True)


def as_list(value):
    return value if isinstance(value, list) else ([] if value is None else [value])


def checked_url(url):
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname not in HOSTS or p.username or p.password or p.port not in (None, 443):
        raise ValueError(f"只支持这两个网站的 HTTPS URL: {url}")
    return urlunsplit(("https", p.hostname, p.path or "/", p.query, ""))


def group_url(url):
    p = urlsplit(checked_url(url))
    return urlunsplit((p.scheme, p.netloc, p.path.rstrip("/") or "/", "", ""))


def site_name(url):
    return "zooplus" if urlsplit(url).hostname in {"zooplus.fi", "www.zooplus.fi"} else "peten"


def is_product_url(url):
    p = urlsplit(url).path
    return bool(re.fullmatch(r"/shop/kissat/.+/\d+/?", p) if site_name(url) == "zooplus"
                else re.fullmatch(r"/tuote/[^/]+", p))


class CrawlError(Exception):
    pass


class HostStopped(CrawlError):
    pass


class Robots:
    """Longest-match robots rules, including * and terminal $, with allow ties.

    Groups with the most specific matching user-agent take precedence. Unknown
    robots responses are handled by Client, not silently treated as permission.
    """
    def __init__(self, text, user_agent):
        groups, agents, rules, delay = [], [], [], 0.0
        has_directive = False
        for raw in text.splitlines() + ["User-agent: __END__"]:
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (x.strip() for x in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if agents and has_directive:
                    groups.append((agents, rules, delay))
                    agents, rules, delay, has_directive = [], [], 0.0, False
                agents.append(value.lower())
            elif agents:
                has_directive = True
                if key in ("allow", "disallow") and value:
                    rules.append((key == "allow", value))
                elif key == "crawl-delay":
                    try:
                        delay = max(0.0, float(value))
                    except ValueError:
                        pass
        ua = user_agent.lower()
        scored = [(max((0 if a == "*" else len(a) for a in ag if a == "*" or a in ua), default=-1), rs, d)
                  for ag, rs, d in groups]
        best = max((x[0] for x in scored), default=-1)
        self.rules = [r for score, rs, _ in scored if score == best and score >= 0 for r in rs]
        self.delay = max((d for score, _, d in scored if score == best and score >= 0), default=0)

    def allowed(self, url):
        p = urlsplit(url)
        target = p.path + ("?" + p.query if p.query else "")
        matches = []
        for allow, pattern in self.rules:
            end = pattern.endswith("$")
            raw = pattern[:-1] if end else pattern
            regex = "^" + ".*".join(re.escape(x) for x in raw.split("*")) + ("$" if end else "")
            if re.search(regex, target):
                matches.append((len(raw.replace("*", "")), allow))
        return max(matches)[1] if matches else True


class Client:
    def __init__(self, cache, delay=5, timeout=30, ttl=86400, user_agent="CatFoodDemoBot/1.0", max_requests=80):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.delay, self.timeout, self.ttl = max(2, delay), timeout, ttl
        self.ua, self.max_requests = user_agent, max_requests
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Language": "fi-FI,fi;q=0.9"})
        self.robots, self.last, self.stopped, self.requests = {}, {}, set(), 0

    def stop(self, url, reason):
        self.stopped.add(site_name(url))
        raise HostStopped(reason)

    def request(self, url):
        url = checked_url(url)
        site = site_name(url)
        if site in self.stopped:
            raise HostStopped(f"{site} 本次运行已停止")
        if self.requests >= self.max_requests:
            raise CrawlError("已达到 --max-requests 请求上限")
        origin = urlsplit(url).netloc
        robots_delay = self.robots[origin].delay if origin in self.robots else 0
        time.sleep(max(0, max(random.uniform(2, 5), self.delay if self.delay > 5 else 0, robots_delay) - (time.monotonic() - self.last.get(site, 0))))
        self.requests += 1
        self.last[site] = time.monotonic()
        try:
            r = self.session.get(url, timeout=(self.timeout, self.timeout), allow_redirects=False, stream=True)
            with r:
                if r.status_code in (401, 403, 429):
                    self.stop(url, f"HTTP {r.status_code}，停止 {site}；不自动重试或绕过")
                body = bytearray()
                for chunk in r.iter_content(65536):
                    body.extend(chunk)
                    if len(body) > 20 * 1024 * 1024:
                        raise CrawlError("页面超过20 MiB，停止读取")
                return r.status_code, {k.lower(): v for k, v in r.headers.items()}, bytes(body).decode("utf-8", errors="replace")
        except requests.RequestException as exc:
            raise CrawlError(f"请求失败: {exc}") from exc

    def rules_for(self, url):
        p = urlsplit(checked_url(url))
        if p.netloc in self.robots:
            return self.robots[p.netloc]
        robots_url = f"https://{p.netloc}/robots.txt"
        # Robots redirects may change www, but must remain on the same retailer.
        for _ in range(5):
            code, headers, body = self.request(robots_url)
            if code in (301, 302, 303, 307, 308) and headers.get("location"):
                dest = checked_url(urljoin(robots_url, headers["location"]))
                if site_name(dest) != site_name(url):
                    self.stop(url, "robots.txt 跳转至其他网站")
                robots_url = dest
                continue
            if code == 404:
                body = ""  # No published robots file.
            elif code != 200 or re.search(r"<html\b|<!doctype\s+html", body, re.I):
                self.stop(url, f"无法确认 robots.txt（HTTP {code}），保守停止")
            rules = Robots(body, self.ua)
            fingerprint = hashlib.sha256(json.dumps([rules.rules, rules.delay], sort_keys=True).encode()).hexdigest()
            baseline = self.cache / ("robots_" + p.netloc + ".sha256")
            if baseline.exists() and baseline.read_text().strip() != fingerprint:
                self.stop(url, "robots.txt 规则变化，停止本轮；人工核对后删除对应 robots_*.sha256 以重新建基线")
            baseline.write_text(fingerprint)
            self.robots[p.netloc] = rules
            return rules
        self.stop(url, "robots.txt 重定向过多")

    def get(self, url):
        original = checked_url(url)
        url = original
        for _ in range(6):
            if not self.rules_for(url).allowed(url):
                raise CrawlError(f"robots.txt 不允许抓取: {url}")
            key = hashlib.sha256(url.encode()).hexdigest()
            meta_path, body_path = self.cache / (key + ".json"), self.cache / (key + ".html")
            if self.ttl > 0 and meta_path.exists() and body_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if time.time() - meta["cached_at"] < self.ttl:
                    LOG.info("CACHE %s", url)
                    return body_path.read_text(encoding="utf-8"), meta
            LOG.info("GET %s", url)
            code, headers, body = self.request(url)
            if code in (301, 302, 303, 307, 308) and headers.get("location"):
                dest = checked_url(urljoin(url, headers["location"]))
                if site_name(dest) != site_name(original):
                    raise CrawlError("拒绝跨零售商重定向")
                url = dest
                continue
            if code != 200:
                raise CrawlError(f"HTTP {code}: {url}")
            title_match = re.search(r"<title\b[^>]*>(.*?)</title>", body[:30000], re.I | re.S)
            title = text_html(title_match.group(1)) if title_match else ""
            if re.search(r"just a moment|access denied|verify you are human|attention required|captcha", title, re.I):
                self.stop(url, "检测到验证或访问限制页面，停止该网站")
            meta = {"url": url, "fetched_at": now(), "cached_at": time.time(),
                    "sha256": hashlib.sha256(body.encode()).hexdigest()}
            body_path.write_text(body, encoding="utf-8")
            meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            return body, meta
        raise CrawlError("页面重定向过多")


def schema_nodes(soup):
    nodes = []
    def add(value):
        if isinstance(value, list):
            for v in value:
                add(v)
        elif isinstance(value, dict):
            nodes.append(value)
            if "@graph" in value:
                add(value["@graph"])
            if isinstance(value.get("mainEntity"), (list, dict)):
                add(value["mainEntity"])
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            add(json.loads(tag.string or tag.get_text()))
        except (ValueError, TypeError):
            continue
    return nodes


def has_type(node, kind):
    return kind in as_list(node.get("@type"))


def properties(node):
    return {p.get("name"): p.get("value") for p in as_list(node.get("additionalProperty")) if isinstance(p, dict)}


def percent_observations(text, location):
    """Only extract explicitly %-marked named nutrients, never guess units."""
    found = []
    for nutrient, label in CORE.items():
        regex = rf"(?<!\w)(?:{label})\s*[:：]?\s*(?P<qual>≥|≤|>=|<=|>|<|min\.?|max\.?|vähintään|enintään)?\s*(?P<n>\d+(?:[.,]\d+)?)\s*%"
        for m in re.finditer(regex, text, re.I):
            q = (m.group("qual") or "").lower()
            kind = "minimum" if q in ("≥", ">=", "min", "min.", "vähintään") else (
                "maximum" if q in ("≤", "<=", "max", "max.", "enintään") else "unspecified")
            n = float(m.group("n").replace(",", "."))
            found.append({"nutrient": nutrient, "value": n, "unit": "%", "value_type": kind,
                          "qualifier_raw": q or None, "raw": m.group(), "location": location,
                          "needs_review": n > 100 or q in ("<", ">")})
    return found


def make_block(scope, raw, location, variant=None, source="page"):
    return {"scope": scope, "variant": variant, "source": source,
            "raw": raw, "observations": percent_observations(raw, location)}


def extract_zooplus(soup):
    # 2026-10 起 Zooplus 将 #ingredients 改为 #ingredients-panel；两者都接受。
    section = soup.find(id="ingredients") or soup.find(id="ingredients-panel")
    if not section:
        return [], []
    # The prose and the currently rendered variant table are separate evidence.
    prose_node = section.select_one('div[class*="anchors_anchorsHTML"]')
    if prose_node:
        prose = prose_node.get_text("\n", strip=True)
    else:
        clone = BeautifulSoup(str(section), "html.parser")
        for t in clone.select("table, select, script, style"):
            t.decompose()
        prose = clone.get_text("\n", strip=True)
    ingredients = re.split(r"\b(?:Lisäaineet|Analyyttiset ainesosat)\b", prose, maxsplit=1, flags=re.I)[0]
    ingredients = re.sub(r"^\s*(?:Valmistusaineet|Koostumus|Ainesosat)\s*[:：]?", "", ingredients, flags=re.I).strip()
    ingredient_blocks = [{"scope": "page_description", "variant": None,
                          "raw": ingredients, "source": "html:#ingredients"}] if ingredients else []
    blocks = []
    analytical = re.split(r"Analyyttiset ainesosat\s*[:：]?", prose, maxsplit=1, flags=re.I)
    if len(analytical) == 2:
        blocks.append(make_block("page_description", analytical[1].strip(), "html:analytical_text"))
    select = section.select_one('select[data-zta="constituentsSelect"]')
    option = (select.select_one("option[selected]") or select.find("option")) if select else None
    label = option.get_text(" ", strip=True) if option else None
    for i, table in enumerate(section.select('table[data-zta="constituentsTable"]')):
        raw = "\n".join(" ".join(cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"]))
                        for tr in table.find_all("tr"))
        block = make_block("rendered_table_variant", raw, f"html:constituentsTable:{i}", label)
        block["variant_selection_note"] = "selected HTML option; first option if no selected attribute"
        blocks.append(block)
    return ingredient_blocks, blocks


def extract_offers(node):
    results = []
    for offer in as_list(node.get("offers")):
        if not isinstance(offer, dict):
            continue
        # Keep aggregate prices and membership conditions distinct from a price.
        kept = {k: offer[k] for k in ("@type", "url", "price", "priceCurrency", "availability",
                "lowPrice", "highPrice", "offerCount", "priceValidUntil", "priceSpecification", "validForMemberTier", "eligibleQuantity", "priceType") if k in offer}
        kept["price_source"] = "jsonld:offers; verify checkout conditions"
        results.append(kept)
    return results


def product_text(soup, site, schema_description=""):
    selectors = ('#description, #feedingrecommendation, #ingredients, #description-panel, '
                 '#feedingrecommendation-panel, #ingredients-panel') if site == 'zooplus' else '.html-reset'
    sections = [node.get_text("\n", strip=True) for node in soup.select(selectors)]
    return "\n".join([text_html(schema_description)] + sections)


def parse_product(html, url, fetched_at=None):
    soup = BeautifulSoup(html, "html.parser")
    nodes = schema_nodes(soup)
    candidates = [n for n in nodes if has_type(n, "ProductGroup") or has_type(n, "Product")]
    if not candidates:
        raise CrawlError("找不到 Product/ProductGroup JSON-LD，未把空页面当作成功")
    target = urlsplit(url).path.rstrip("/")
    exact = [n for n in candidates if urlsplit(str(n.get("url", n.get("@id", "")))).path.rstrip("/") == target]
    if exact:
        product = next((n for n in exact if has_type(n, "ProductGroup")), exact[0])
    else:
        h1 = soup.find("h1")
        title = clean(h1.get_text()) if h1 else ""
        named = [n for n in candidates if title and clean(n.get("name")) == title]
        if len(named) != 1:
            raise CrawlError("无法将结构化产品与当前页面匹配")
        product = named[0]
    crumbs = []
    for n in nodes:
        if has_type(n, "BreadcrumbList"):
            for item in n.get("itemListElement", []):
                value = item.get("item")
                crumbs.append(clean(item.get("name") or (value.get("name") if isinstance(value, dict) else "")))
    cat_context = " ".join(crumbs + [str(product.get("category", ""))]).lower()
    if site_name(url) == "zooplus":
        cat_food = "/shop/kissat/kuivaruoka/" in target or "/shop/kissat/markaruoka/" in target
        cat_food = cat_food or ("kissan" in cat_context and "ruo" in cat_context)
    else:
        cat_food = "kissan" in cat_context and "ruo" in cat_context
    if not cat_food:
        raise CrawlError("未能确认是猫粮，跳过；请检查面包屑与商品类别")
    warnings, variants, ingredient_blocks, nutrition_blocks = [], [], [], []
    if site_name(url) == "zooplus":
        ingredient_blocks, nutrition_blocks = extract_zooplus(soup)
    for node in as_list(product.get("hasVariant")) or [product]:
        if not isinstance(node, dict):
            continue
        props = properties(node)
        sku = str(node.get("sku", "")) or None
        variant_id = sku or node.get("url") or node.get("@id") or node.get("name")
        variant = {"sku": sku, "name": node.get("name"), "url": node.get("url") or node.get("@id"),
                   "gtin": node.get("gtin13") or node.get("gtin"), "size_raw": node.get("size"),
                   "offers": extract_offers(node),
                   "attributes": {k: v for k, v in props.items() if k not in ("ingredients", "nutrition", "addedNutrition")}}
        # Peten publishes ingredient/nutrition text per SKU. Do not collapse it
        # into one common recipe, even when today's variants happen to match.
        if props.get("ingredients"):
            ingredient_blocks.append({"scope": "variant", "variant": variant_id,
                                      "raw": text_html(props["ingredients"]), "source": "jsonld:ingredients"})
        if props.get("nutrition"):
            nutrition_blocks.append(make_block("variant", text_html(props["nutrition"]),
                                    "jsonld:additionalProperty:nutrition", variant_id, "jsonld"))
        if props.get("addedNutrition"):
            variant["additives_raw"] = text_html(props["addedNutrition"])
        variants.append(variant)
    # Preserve disagreements rather than selecting whichever text was first.
    observed = {}
    for block in nutrition_blocks:
        for obs in block["observations"]:
            observed.setdefault(obs["nutrient"], set()).add((obs["value"], obs["value_type"]))
    for key, values in observed.items():
        if len(values) > 1:
            warnings.append(f"{key}: different values/qualifiers across blocks or variants; inspect scope")
    if not ingredient_blocks:
        warnings.append("ingredients_missing")
    if not nutrition_blocks:
        warnings.append("nutrition_missing")
    if site_name(url) == "zooplus" and nutrition_blocks:
        warnings.append("zooplus_table_is_rendered_variant_only; not automatically valid for all offers")
    if any(not v["offers"] for v in variants):
        warnings.append("some_variant_offers_missing")
    brand = product.get("brand")
    brand = brand.get("name") if isinstance(brand, dict) else brand
    imgs = [x.get("url") if isinstance(x, dict) else x for x in as_list(product.get("image"))]
    return {"schema_version": VERSION, "source_site": site_name(url), "source_url": url,
            "fetched_at": fetched_at or now(), "product_id": str(product.get("productGroupID") or product.get("sku") or target.split("/")[-1]),
            "name": product.get("name"), "brand": brand, "product_description_raw": product_text(soup, site_name(url), product.get("description", "")), "page_text_snapshot": product_text(soup, site_name(url), product.get("description", "")), "images": [x for x in imgs if x],
            "breadcrumbs": crumbs, "variants": variants, "ingredient_blocks": ingredient_blocks,
            "nutrition_blocks": nutrition_blocks, "warnings": warnings,
            "review_status": "unreviewed", "market": "FI"}


def listing_links(html, url):
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        try:
            target = group_url(urljoin(url, a["href"]))
            if site_name(target) == site_name(url) and is_product_url(target) and target not in links:
                links.append(target)
        except ValueError:
            continue
    next_url = None
    for a in soup.find_all("a", href=True):
        if "next" in as_list(a.get("rel")) or re.fullmatch(r"seuraava|next", clean(a.get_text()), re.I):
            try:
                target = checked_url(urljoin(url, a["href"]))
                if urlsplit(target).path == urlsplit(url).path and site_name(target) == site_name(url):
                    next_url = target
                    break
            except ValueError:
                continue
    return links, next_url


def listing_total(html):
    """Zooplus 分类页自报总数，如 "1 - 48 (Yht. 2466)"；仅用于覆盖率核对。"""
    m = re.search(r"\(Yht\.\s*([\d\s\u00a0]+)\)", html)
    return int(re.sub(r"\D", "", m.group(1))) if m else None


def discover(client, site, pages, candidate_limit, keyword, report=None):
    """Bounded discovery. pages/candidate_limit 为 0 表示不设上限（仍受 --max-requests 约束）。

    Peten sitemap candidates are filtered again by breadcrumb. report 记录每个分类的
    页数、链接数与网站自报总数，写入 run_summary 供核对覆盖率。
    """
    found, seen_links = [], set()
    full = lambda: candidate_limit and len(found) >= candidate_limit
    for category in CATEGORIES[site]:
        url, visited = category, set()
        info = {"pages": 0, "links": 0, "reported_total": None, "complete": False}
        if report is not None:
            report[category] = info
        while url and url not in visited:
            if pages and info["pages"] >= pages:
                break
            visited.add(url)
            body, meta = client.get(url)
            info["pages"] += 1
            if info["reported_total"] is None:
                info["reported_total"] = listing_total(body)
            links, url = listing_links(body, meta["url"])
            if site == "zooplus":
                # 分类页上的推荐位会带入零食/补充剂链接；只保留本分类路径下的商品。
                links = [x for x in links if urlsplit(x).path.startswith(urlsplit(category).path + "/")]
            new = [x for x in links if x not in seen_links]
            seen_links.update(new)
            found.extend(new)
            info["links"] += len(new)
            if full():
                return found[:candidate_limit]
        info["complete"] = not url
        if info["reported_total"] and info["links"] < info["reported_total"]:
            LOG.warning("%s 只发现 %d / %d 个商品链接（%d 页）；如需全量请提高 --pages 或设为 0",
                        category, info["links"], info["reported_total"], info["pages"])
    if site == "peten" and not found:
        LOG.info("Peten 分类页无商品链接，使用公开 sitemap（关键词筛选，不保证完整覆盖）")
        queue = deque(["https://www.petenkoiratarvike.com/sitemap_index.xml"])
        seen = set()
        while queue and len(seen) < 6 and not full():
            sm_url = queue.popleft()
            if sm_url in seen:
                continue
            seen.add(sm_url)
            body, _ = client.get(sm_url)
            try:
                root = ET.fromstring(body)
            except ET.ParseError as exc:
                raise CrawlError(f"sitemap 解析失败: {sm_url}") from exc
            for entry in root:
                loc = next((e.text for e in entry if e.tag.split("}")[-1] == "loc"), None)
                if not loc:
                    continue
                try:
                    target = checked_url(loc)
                except ValueError:
                    continue
                if site_name(target) != site:
                    continue
                if root.tag.split("}")[-1] == "sitemapindex":
                    if "review" not in target and "legacy" not in target:
                        queue.append(target)
                elif is_product_url(target) and re.search(keyword, target, re.I):
                    target = group_url(target)
                    if target not in found:
                        found.append(target)
                    if full():
                        break
    return found[:candidate_limit] if candidate_limit else found


def csv_safe(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def write_csv(path, columns, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: csv_safe(row.get(k)) for k in columns})


def export(products, out, errors, summary):
    out.mkdir(parents=True, exist_ok=True)
    (out / "products.json").write_text(json.dumps(products, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = [{**p, "variant_count": len(p["variants"])} for p in products]
    write_csv(out / "products.csv", ["source_site", "product_id", "name", "brand", "source_url", "fetched_at",
             "variant_count", "images", "ingredient_blocks", "nutrition_blocks", "warnings", "review_status"], rows)
    offers, nutrients = [], []
    for p in products:
        base = {k: p[k] for k in ("source_site", "product_id", "source_url", "fetched_at")}
        for v in p["variants"]:
            for o in v["offers"] or [{}]:
                offers.append({**base, "sku": v["sku"], "name": v["name"], "size_raw": v["size_raw"],
                               "offer_type": o.get("@type"), **{k: o.get(k) for k in ("price", "priceCurrency",
                               "availability", "lowPrice", "highPrice", "priceSpecification", "url")}})
        for block in p["nutrition_blocks"]:
            for obs in block["observations"]:
                nutrients.append({**base, "scope": block["scope"], "variant": block["variant"], **obs})
    write_csv(out / "offers.csv", ["source_site", "product_id", "sku", "name", "size_raw", "offer_type", "price",
              "priceCurrency", "availability", "lowPrice", "highPrice", "priceSpecification", "url", "source_url", "fetched_at"], offers)
    write_csv(out / "nutrients.csv", ["source_site", "product_id", "scope", "variant", "nutrient", "value", "unit",
              "value_type", "qualifier_raw", "raw", "location", "needs_review", "source_url", "fetched_at"], nutrients)
    (out / "errors.json").write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--site", choices=["all", "zooplus", "peten"], default="all")
    p.add_argument("--urls", type=Path, help="每行一个商品URL；默认读取demo_urls.txt")
    p.add_argument("--discover", action="store_true", help="从分类页/sitemap发现URL")
    p.add_argument("--limit", type=int, default=3, help="每个网站最多成功抓取的商品组数，默认3；0 表示不限")
    p.add_argument("--pages", type=int, default=2, help="每个分类最多翻页数；0 表示翻到最后一页")
    p.add_argument("--candidates", type=int, default=30, help="每站最多尝试的候选商品页数；0 表示不限")
    p.add_argument("--keyword", default=r"kissan|cat-|kitten|sterilised|applaws", help="Peten sitemap URL筛选正则")
    p.add_argument("--delay", type=float, default=5, help="同一网站请求间隔秒数，最少3秒")
    p.add_argument("--timeout", type=float, default=30)
    p.add_argument("--max-requests", type=int, default=80)
    p.add_argument("--cache-hours", type=float, default=24, help="0表示重新下载页面，robots每次运行重新检查")
    p.add_argument("--user-agent", default="", help="必填 PawBase 爬虫标识和真实联系邮箱")
    p.add_argument("--database", type=Path, help="通过质量门禁写入 Food Guide SQLite")
    p.add_argument("--output", type=Path, default=Path("data"))
    p.add_argument("--cache", type=Path, default=Path(".cache"))
    p.add_argument("--resume", action="store_true", help="保留 --output 中已有的 products.json，只抓取其中没有的商品；新旧结果合并写回并整体导入数据库（已有记录幂等更新）")
    args = p.parse_args(argv)
    if "PawBase" not in args.user_agent or not re.search(r"[^\s@]+@[^\s@]+\.[^\s@)]+", args.user_agent):
        p.error("--user-agent 必须包含 PawBase 和真实联系邮箱")
    if min(args.limit, args.pages, args.candidates) < 0 or args.max_requests < 1 or args.timeout <= 0 or args.cache_hours < 0:
        p.error("数量不能为负（0 表示不限），--max-requests 与超时必须为正，缓存时间不能为负")
    if args.urls and args.discover:
        p.error("--urls 与 --discover 二选一")
    try:
        re.compile(args.keyword)
    except re.error as exc:
        p.error(f"--keyword 正则错误: {exc}")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sites = ["zooplus", "peten"] if args.site == "all" else [args.site]
    client = Client(args.cache, args.delay, args.timeout, args.cache_hours * 3600, args.user_agent, args.max_requests)
    products, errors, existing = [], [], []
    if args.resume and (args.output / "products.json").exists():
        try:
            existing = json.loads((args.output / "products.json").read_text(encoding="utf-8"))
            done = {group_url(x["source_url"]) for x in existing}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            p.error(f"--resume 无法读取已有 products.json: {exc}")
    else:
        done = set()
    counts = {site: 0 for site in sites}
    seeds = []
    site_seeds = {site: [] for site in sites}
    if not args.discover:
        path = args.urls or Path(__file__).with_name("demo_urls.txt")
        try:
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                url = group_url(line.strip())
                if not is_product_url(url):
                    raise ValueError(f"不是支持的商品URL: {url}")
                if url not in seeds:
                    seeds.append(url)
                    site_seeds.setdefault(site_name(url), []).append(url)
        except (OSError, ValueError) as exc:
            p.error(str(exc))
    started = now()
    discovery = {}
    try:
        for site in sites:
            try:
                candidates = (discover(client, site, args.pages, args.candidates, args.keyword, discovery.setdefault(site, {}))
                              if args.discover else site_seeds[site][:args.candidates or None])
                candidates = [u for u in candidates if u not in done]
                site_seeds[site] = candidates
                if not candidates:
                    errors.append({"site": site, "error": "没有找到商品URL，请使用 --urls 指定链接"})
                seen = set()
                for url in candidates:
                    if args.limit and counts[site] >= args.limit:
                        break
                    meta = None
                    try:
                        html, meta = client.get(url)
                        key = group_url(meta["url"])
                        if key in seen or key in done:
                            continue
                        seen.add(key)
                        item = parse_product(html, meta["url"], meta["fetched_at"])
                        item["source_html_sha256"] = meta["sha256"]
                        products.append(item)
                        counts[site] += 1
                        LOG.info("OK %s (%d variants, %d warnings)", item["name"], len(item["variants"]), len(item["warnings"]))
                    except HostStopped:
                        raise
                    except (CrawlError, ValueError) as exc:
                        error = {"site": site, "url": url, "error": str(exc)}
                        if meta and meta["url"] != url:
                            error["final_url"] = meta["url"]  # 重定向后的实际页面，便于排查
                        errors.append(error)
                        LOG.warning("SKIP %s: %s", url, exc)
            except HostStopped:
                raise
            except (CrawlError, ValueError) as exc:
                errors.append({"site": site, "error": str(exc)})
                LOG.warning("STOP %s: %s", site, exc)
    except HostStopped as exc:
        errors.append({"error": str(exc), "fatal": True})
    except KeyboardInterrupt:
        errors.append({"error": "用户中断；已保存完成的商品", "fatal": True})
    finally:
        effective_limits = {site: (min(args.limit, len(site_seeds.get(site, []))) if args.limit and (args.resume or not args.discover)
                                   else args.limit or len(site_seeds.get(site, []))) for site in sites}
        short = {s: {"requested": effective_limits[s], "fetched": n} for s, n in counts.items() if n < effective_limits[s]}
        summary = {"started_at": started, "finished_at": now(), "counts": counts,
                   "requests": client.requests, "shortfall": short, "error_count": len(errors),
                   "status": "partial" if errors or short else "complete"}
        if args.resume:
            summary["resumed_existing"] = len(existing)
        if discovery:
            summary["discovery"] = discovery
        export(existing + products, args.output, errors, summary)
    if args.database:
        from database import Database, now as db_now, encode
        from pipeline import import_products
        db = Database(args.database)
        if any(e.get('fatal') for e in errors):
            with db.connect() as conn:
                conn.execute('INSERT INTO quarantine(created_at,source,reason,payload) VALUES(?,?,?,?)',
                             (db_now(), args.site, encode(errors), encode(products)))
            summary['ingest'] = {'status': 'quarantined', 'reason': '访问限制或 robots 变化，本轮不入主库'}
        else:
            summary['ingest'] = import_products(db, existing + products, len(errors), errors)
        (args.output / 'run_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    ingest = summary.get('ingest', [])
    quarantined = (isinstance(ingest, dict) and ingest.get('status') == 'quarantined') or (isinstance(ingest, list) and any(x.get('status') == 'quarantined' for x in ingest))
    return 2 if quarantined else (0 if not errors and not short else (2 if products else 1))


if __name__ == "__main__":
    sys.exit(main())
