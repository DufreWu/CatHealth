import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import crawler as c

Z = 'https://www.zooplus.fi/shop/kissat/kuivaruoka/brand/adult/12345'
P = 'https://www.petenkoiratarvike.com/tuote/demo-cat-P123'


def fixture(url=Z, cat=True):
    group = {'@type': 'ProductGroup', 'name': 'Demo food', 'url': url, 'productGroupID': '123',
             'hasVariant': [{'@type': 'Product', 'sku': 'small', 'name': 'Demo 400 g',
                 'offers': {'@type': 'Offer', 'price': 5.99, 'priceCurrency': 'EUR',
                            'priceSpecification': [{'price': 5.00, 'validForMemberTier': {'name': 'member'}}]},
                 'additionalProperty': [
                     {'name': 'ingredients', 'value': 'Demo ingredient list'},
                     {'name': 'nutrition', 'value': 'Raakavalkuainen: 37% - Tuhka: 8,2%'}]}]}
    crumbs = {'@type': 'BreadcrumbList', 'itemListElement': [{'name': 'Kissanruoka' if cat else 'Koiranruoka'}]}
    return '<h1>Demo food</h1><script type="application/ld+json">' + json.dumps({'@graph': [crumbs, group]}) + '</script>'


class ParserTests(unittest.TestCase):
    def test_variant_price_and_raw_ingredients(self):
        p = c.parse_product(fixture(P), P)
        self.assertEqual(p['variants'][0]['offers'][0]['price'], 5.99)
        self.assertEqual(p['variants'][0]['offers'][0]['priceSpecification'][0]['price'], 5)
        self.assertEqual(p['ingredient_blocks'][0]['variant'], 'small')
        self.assertEqual(p['nutrition_blocks'][0]['observations'][1]['value'], 8.2)

    def test_missing_product_and_wrong_animal(self):
        with self.assertRaises(c.CrawlError):
            c.parse_product('<h1>Please enable JS</h1>', P)
        with self.assertRaises(c.CrawlError):
            c.parse_product(fixture(P, cat=False), P)

    def test_percent_units_and_comparators(self):
        obs = c.percent_observations('Raakaproteiini: min. 37 %; kosteus ≤ 10,5 %; tauriini 2 mg/kg', 'test')
        self.assertEqual([(o['nutrient'], o['value'], o['value_type']) for o in obs],
                         [('protein', 37, 'minimum'), ('moisture', 10.5, 'maximum')])
        self.assertEqual(c.percent_observations('proteiini 20; kcal 370', 'test'), [])

    def test_conflict_is_retained_and_table_scoped(self):
        section = '''<div id="ingredients"><div class="anchors_anchorsHTML_test">
        <b>Valmistusaineet</b> Demo ingredients <b>Lisäaineet</b> zinc
        <b>Analyyttiset ainesosat</b> Raakakuitu: 6,2 %</div>
        <select data-zta="constituentsSelect"><option>400 g</option><option>2 kg</option></select>
        <table data-zta="constituentsTable"><tr><td>raakakuitu</td><td>6.1 %</td></tr></table></div>'''
        p = c.parse_product(fixture() + section, Z)
        self.assertTrue(any('fiber:' in x for x in p['warnings']))
        table = next(b for b in p['nutrition_blocks'] if b['scope'] == 'rendered_table_variant')
        self.assertEqual(table['variant'], '400 g')
        self.assertNotIn('zinc', p['ingredient_blocks'][0]['raw'])

    def test_zooplus_panel_layout(self):
        # 2026-10 Zooplus 布局：#ingredients-panel，无“Analyyttiset ainesosat”文字段
        section = '''<div id="description-panel">Täysravinto aikuisille kissoille.</div>
        <div id="ingredients-panel"><div data-zta="productIngredients" class="anchors_anchorsHTML___x">
        <b>Koostumus</b><br>Kana 40 %<br><b>Lisäaineet</b><br>zinc</div>
        <select data-zta="constituentsSelect"><option value="2 kg">2 kg</option></select>
        <table data-zta="constituentsTable"><tr><td>raakaproteiini</td><td>36.0 %</td></tr></table></div>'''
        p = c.parse_product(fixture() + section, Z)
        self.assertEqual(p['ingredient_blocks'][0]['raw'], 'Kana 40 %')
        table = next(b for b in p['nutrition_blocks'] if b['scope'] == 'rendered_table_variant')
        self.assertEqual((table['variant'], table['observations'][0]['value']), ('2 kg', 36))
        self.assertIn('Täysravinto', p['page_text_snapshot'])

    def test_discover_all_categories_until_last_page(self):
        z2 = Z.replace('12345', '67890')
        wet = 'https://www.zooplus.fi/shop/kissat/markaruoka/brand/adult/11111'
        pages = {c.CATEGORIES['zooplus'][0]: f'(Yht. 2)<a href="{Z}">a</a><a rel="next" href="?p=2">n</a>',
                 c.CATEGORIES['zooplus'][0] + '?p=2': f'<a href="{z2}">b</a><a href="{Z}">dup</a>',
                 c.CATEGORIES['zooplus'][1]: f'(Yht. 1)<a href="{wet}">w</a>'}
        client = MagicMock()
        client.get.side_effect = lambda url: (pages[url], {'url': url})
        report = {}
        self.assertEqual(c.discover(client, 'zooplus', 0, 0, '', report), [Z, z2, wet])
        dry = report[c.CATEGORIES['zooplus'][0]]
        self.assertEqual((dry['pages'], dry['links'], dry['reported_total'], dry['complete']), (2, 2, 2, True))
        self.assertEqual(c.discover(client, 'zooplus', 1, 0, '', {}), [Z, wet])
        self.assertEqual(c.discover(client, 'zooplus', 0, 2, ''), [Z, z2])

    def test_discover_drops_links_outside_category(self):
        treat = 'https://www.zooplus.fi/shop/kissat/herkut/brand/12345'
        client = MagicMock()
        client.get.side_effect = lambda url: (f'<a href="{treat}">t</a><a href="{Z}">z</a>', {'url': url})
        self.assertEqual(c.discover(client, 'zooplus', 1, 0, ''), [Z])

    def test_resume_keeps_existing_and_fetches_only_missing(self):
        z2 = Z.replace('12345', '67890')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            urls = root / 'urls.txt'
            urls.write_text(f'{Z}\n{z2}\n', encoding='utf-8')
            out = root / 'out'
            out.mkdir()
            old = c.parse_product(fixture(Z), Z, '2026-10-01T00:00:00+00:00')
            (out / 'products.json').write_text(json.dumps([old]), encoding='utf-8')
            fetched = []

            def fake_get(self, url):
                fetched.append(url)
                return fixture(url), {'url': url, 'fetched_at': c.now(), 'sha256': 'hash'}

            with patch.object(c.Client, 'get', autospec=True, side_effect=fake_get):
                result = c.main(['--site', 'zooplus', '--urls', str(urls), '--limit', '0', '--resume',
                                 '--user-agent', 'PawBaseBot/0.1 (contact: user@example.com)', '--output', str(out)])
            self.assertEqual(result, 0)
            self.assertEqual(fetched, [z2])
            merged = json.loads((out / 'products.json').read_text())
            self.assertEqual([p['source_url'] for p in merged], [Z, z2])
            self.assertEqual(merged[0]['fetched_at'], '2026-10-01T00:00:00+00:00')
            summary = json.loads((out / 'run_summary.json').read_text())
            self.assertEqual((summary['counts'], summary['resumed_existing'], summary['shortfall']), ({'zooplus': 1}, 1, {}))

    def test_listing_dedupe_and_pagination(self):
        html = '<a href="' + Z + '?activeVariant=1">a</a><a href="' + Z + '">b</a>'
        html += '<a href="/shop/kissat/kuivaruoka/4vets">brand</a><a rel="next" href="?p=2">next</a>'
        links, nxt = c.listing_links(html, c.CATEGORIES['zooplus'][0])
        self.assertEqual(links, [Z])
        self.assertTrue(nxt.endswith('?p=2'))

    def test_url_boundaries(self):
        for u in ['http://www.zooplus.fi/', 'https://www.zooplus.fi.evil.org/', 'https://user@www.zooplus.fi/', 'https://www.zooplus.fi:8443/']:
            with self.assertRaises(ValueError):
                c.checked_url(u)

    def test_export_and_csv_formula_injection(self):
        p = c.parse_product(fixture(P), P)
        p['name'] = '=HYPERLINK("https://example.com")'
        with tempfile.TemporaryDirectory() as d:
            c.export([p], Path(d), [], {'status': 'complete'})
            self.assertEqual(json.loads((Path(d)/'products.json').read_text())[0]['name'], p['name'])
            self.assertIn("'=HYPERLINK", (Path(d)/'products.csv').read_text(encoding='utf-8-sig'))
            self.assertTrue((Path(d)/'offers.csv').exists())
        self.assertEqual(c.csv_safe('  +SUM(1)'), "'  +SUM(1)")


class AccessTests(unittest.TestCase):
    def test_robots_wildcard_specificity(self):
        r = c.Robots('User-agent: *\nAllow: /\nDisallow: /*?q=*\nDisallow: /mobify/\n', 'CatFoodDemoBot/1.0')
        self.assertFalse(r.allowed('https://www.petenkoiratarvike.com/a?q=cat'))
        self.assertFalse(r.allowed('https://www.petenkoiratarvike.com/mobify/data'))
        self.assertTrue(r.allowed(P))

    def test_robots_longest_match_and_agent(self):
        txt = 'User-agent: *\nDisallow: /\nUser-agent: CatFoodDemoBot\nDisallow: /private\nAllow: /private/open$\nCrawl-delay: 8\n'
        r = c.Robots(txt, 'CatFoodDemoBot/1.0')
        self.assertTrue(r.allowed(P))
        self.assertTrue(r.allowed('https://www.zooplus.fi/private/open'))
        self.assertFalse(r.allowed('https://www.zooplus.fi/private/open/file'))
        self.assertEqual(r.delay, 8)

    def test_robots_unknown_response_stops_host(self):
        with tempfile.TemporaryDirectory() as d:
            client = c.Client(d)
            with patch.object(client, 'request', return_value=(503, {}, 'unavailable')):
                with self.assertRaises(c.HostStopped):
                    client.get(P)
            self.assertIn('peten', client.stopped)

    def test_disallowed_url_never_requested(self):
        with tempfile.TemporaryDirectory() as d:
            client = c.Client(d)
            with patch.object(client, 'request', return_value=(200, {}, 'User-agent: *\nDisallow: /')) as req:
                with self.assertRaises(c.CrawlError):
                    client.get(P)
                self.assertEqual(req.call_count, 1)  # robots only

    def test_robots_change_stops_next_run(self):
        with tempfile.TemporaryDirectory() as d:
            first = c.Client(d)
            with patch.object(first, 'request', return_value=(200, {}, 'User-agent: *\nAllow: /')):
                first.rules_for(P)
            second = c.Client(d)
            with patch.object(second, 'request', return_value=(200, {}, 'User-agent: *\nDisallow: /')):
                with self.assertRaises(c.HostStopped):
                    second.rules_for(P)
            self.assertIn('peten', second.stopped)

    def test_cache_keeps_original_timestamp(self):
        with tempfile.TemporaryDirectory() as d:
            client = c.Client(d)
            with patch.object(client, 'request', side_effect=[(200, {}, 'User-agent: *\nAllow: /'), (200, {}, fixture(P))]) as req:
                first = client.get(P)
                second = client.get(P)
                self.assertEqual(first, second)
                self.assertEqual(req.call_count, 2)

    def test_rate_limit_stops_without_retry(self):
        with tempfile.TemporaryDirectory() as d:
            client = c.Client(d)
            response = MagicMock()
            response.status_code = 429
            with patch.object(client.session, 'get', return_value=response) as get:
                with self.assertRaises(c.HostStopped):
                    client.request(P)
                with self.assertRaises(c.HostStopped):
                    client.request(P)
                self.assertEqual(get.call_count, 1)

    def test_html_challenge_not_cached(self):
        with tempfile.TemporaryDirectory() as d:
            client = c.Client(d)
            with patch.object(client, 'request', side_effect=[(200, {}, ''), (200, {}, '<title>Just a moment...</title>')]):
                with self.assertRaises(c.HostStopped):
                    client.get(P)
            self.assertEqual(list(Path(d).glob('*.html')), [])

    def test_explicit_urls_do_not_create_shortfall_when_limit_exceeds_available(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            urls = root / 'demo_urls.txt'
            urls.write_text(f'{Z}\n{P}\n', encoding='utf-8')
            out = root / 'out'
            out.mkdir()

            def fake_get(self, url):
                return fixture(url), {'url': url, 'fetched_at': c.now(), 'sha256': 'hash'}

            with patch.object(c.Client, 'get', autospec=True, side_effect=fake_get):
                result = c.main([
                    '--site', 'all',
                    '--urls', str(urls),
                    '--limit', '300',
                    '--cache-hours', '0',
                    '--user-agent', 'PawBaseBot/0.1 (contact: user@example.com)',
                    '--output', str(out),
                ])

            self.assertEqual(result, 0)
            summary = json.loads((out / 'run_summary.json').read_text())
            self.assertEqual(summary['counts'], {'zooplus': 1, 'peten': 1})
            self.assertEqual(summary['shortfall'], {})


if __name__ == '__main__':
    unittest.main()
