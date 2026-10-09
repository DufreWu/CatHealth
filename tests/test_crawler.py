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

    def test_listing_dedupe_and_pagination(self):
        html = '<a href="' + Z + '?activeVariant=1">a</a><a href="' + Z + '">b</a>'
        html += '<a href="/shop/kissat/kuivaruoka/4vets">brand</a><a rel="next" href="?p=2">next</a>'
        links, nxt = c.listing_links(html, c.CATEGORIES['zooplus'])
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


if __name__ == '__main__':
    unittest.main()
