import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from nutrition import parse_nutrients, validate, config, band_for, dimension_config, analyze, verdicts, context_notes, convert_reference, legal_max
from pipeline import normalize, import_products
from database import Database, stats
from app import handler_for
from fixtures import product

class NutritionTests(unittest.TestCase):
    def test_units_missing_and_bounds(self):
        v,issues,_=parse_nutrients('protein 315 g/kg; fat 16,5 g/100g; magnesium 800 mg/kg; moisture 8%')
        self.assertEqual(v['protein'],31.5)
        self.assertEqual(v['fat'],16.5)
        self.assertAlmostEqual(v['magnesium'],.08)
        self.assertIsNone(v['ash'])
        self.assertFalse(issues)
        # Peten 标签常写 “valkuainen”；带前缀的 raakavalkuainen 不应被重复计数
        self.assertEqual(parse_nutrients('RAVINTOAINEET: valkuainen 43 %, raakarasva 23,5 %')[0]['protein'],43)
        v,issues,_=parse_nutrients('Raakavalkuainen 34,5%')
        self.assertEqual((v['protein'],issues),(34.5,[]))
        v,issues,_=parse_nutrients('protein >= 30%; moisture 8%')
        self.assertIsNone(v['protein']);self.assertTrue(issues)

    def test_sum_and_conflicting_labels(self):
        v,issues,_=parse_nutrients('protein 50%; fat 30%; fiber 5%; ash 10%; moisture 10%')
        self.assertTrue(validate(v,'dry')[0])
        v,issues,_=parse_nutrients('protein 30%; protein 31%')
        self.assertIsNone(v['protein']);self.assertTrue(issues)

    def test_exclusion_not_guessed(self):
        p=product();p['product_description_raw']='Food for cats'
        self.assertEqual(normalize(p)[1][0]['reason'],'declaration_not_found')
        p['product_description_raw']='Complementary food. Complete food.'
        self.assertEqual(normalize(p)[1][0]['reason'],'complementary')

    def test_exact_boundaries_and_kitten(self):
        cfg=config();dim=cfg['dimensions']['protein_dm_pct']
        self.assertEqual(band_for(25,dim)['label'],'meets_minimum')
        self.assertEqual(band_for(40,dim)['label'],'moderate')
        self.assertEqual(band_for(40.01,dim)['label'],'high')
        kitten=dimension_config(cfg,'kitten')['protein_dm_pct']
        self.assertEqual(band_for(27,kitten)['label'],'below_minimum')
        self.assertEqual(kitten['fediaf_lines'][0]['value'],28)

    def analyzed(self, nutrition=None, stage='adult'):
        r=normalize(product(stage=stage,nutrition=nutrition))[0][0];r['analysis']=analyze(r);return r

    def test_energy_basis_and_sodium(self):
        a=self.analyzed('protein 36%; fat 16%; fiber 3%; ash 7%; moisture 8%; phosphorus 1.0%; calcium 1.2%; Natrium 0,38%')['analysis']
        # NRC 2006: GE 490.9, digestibility 87.9-0.88*3.26087, ME = DE - 0.77*36
        self.assertAlmostEqual(a['me_kcal_per_100g'],389.6944,places=3)
        self.assertAlmostEqual(a['phosphorus_g_per_1000kcal'],1.0/a['me_kcal_per_100g']*1000,places=5)
        self.assertAlmostEqual(a['sodium_g_per_1000kcal'],0.38/a['me_kcal_per_100g']*1000,places=5)
        # ME needs all five declared values; moisture is never estimated.
        a=self.analyzed('protein 36%; fat 16%; fiber 3%; ash 7%; phosphorus 1.0%')['analysis']
        self.assertIsNone(a['me_kcal_per_100g']);self.assertIsNone(a['phosphorus_g_per_1000kcal'])

    def test_carbohydrate_not_displayed(self):
        cfg=config()
        self.assertNotIn('carbs_est_dm_pct',cfg['dimensions'])
        self.assertIsNotNone(self.analyzed()['analysis']['carbs_est_dm_pct'])

    def test_verdict_levels(self):
        cfg=config()
        self.assertEqual(verdicts(self.analyzed(),cfg)['protein_dm_pct']['level'],'meets_minimum')
        low=verdicts(self.analyzed('protein 30%; fat 16%; fiber 3%; ash 7%; moisture 8%'),cfg)['protein_dm_pct']
        self.assertEqual((low['level'],low['color']),('below_recommended','yellow'))
        self.assertEqual(verdicts(self.analyzed('protein 30%; fat 16%'),cfg)['protein_dm_pct']['level'],'not_assessed')
        # Kitten uses its own minimum; unrounded value exactly at the minimum meets it.
        kitten=self.analyzed('protein 23%; fat 16%; fiber 3%; ash 7%; moisture 17.857142857142858%',stage='kitten')
        kitten['analysis']['protein_dm_pct']=28.0
        self.assertEqual(verdicts(kitten,cfg)['protein_dm_pct']['level'],'meets_minimum')
        cfg=copy.deepcopy(cfg);cfg['verdicts']['protein_dm_pct']['stages']['adult'].update(nutritional_max=38,legal_max=39)
        self.assertEqual(verdicts(self.analyzed(),cfg)['protein_dm_pct']['level'],'above_legal_max')
        cfg['verdicts']['protein_dm_pct']['stages']['adult']['legal_max']=40
        self.assertEqual(verdicts(self.analyzed(),cfg)['protein_dm_pct']['level'],'above_nutritional_max')

    def test_cat_specific_warnings(self):
        cfg=config()
        texts=[r['text'] for r in cfg['context_rules']]
        # Rule order: phosphorus info, phosphorus warning, Ca:P, sodium, taurine.
        notes=lambda n:[(x['level'],texts.index(x['text'])) for x in context_notes(self.analyzed(n),cfg)]
        base='protein 36%; fat 16%; fiber 3%; ash 7%; moisture 8%; '
        self.assertEqual(notes(base+'phosphorus 1.0%; calcium 1.2%'),[])
        self.assertEqual(notes(base+'phosphorus 1.6%; calcium 1.8%'),[('info',0)])
        # Two tiers are exclusive; Ca:P exactly 1:1 does not trigger.
        self.assertEqual(notes(base+'phosphorus 2.0%; calcium 2.0%'),[('warning',1)])
        self.assertEqual(notes(base+'phosphorus 1.0%; calcium 0.9%'),[('warning',2)])
        self.assertEqual(notes(base+'phosphorus 1.0%; calcium 1.2%; sodium 1.5%'),[('info',3)])

    def test_vet_consultation_is_not_prescription(self):
        p=product();p['product_description_raw']+=' Consult your veterinary professional.'
        self.assertFalse(normalize(p)[0][0]['is_prescription'])
        self.assertTrue(normalize(product(prescription=True))[0][0]['is_prescription'])

    def test_reference_price_conditions(self):
        p=product();p['variants'][0]['offers'][0]['validForMemberTier']={'name':'member'}
        self.assertIsNone(normalize(p)[0][0]['offers'][0]['reference_price'])

class ReferenceUnitTests(unittest.TestCase):
    """FEDIAF 2025 Table III-2 系数与维生素 D 法定上限单位（2026-10-26 更正项）。"""
    def setUp(self):
        self.cfg=config()

    def test_conversion_factors_match_energy_basis(self):
        uc=self.cfg['unit_conversion']
        self.assertEqual((uc['kcal_me_per_kg_dm'],uc['kcal_per_mj']),(4000,239))
        kcal_per_100g_dm=uc['kcal_me_per_kg_dm']/10
        mj_per_100g_dm=kcal_per_100g_dm/uc['kcal_per_mj']
        expected={
            'per_100g_dm->per_1000kcal':1000/kcal_per_100g_dm,
            'per_1000kcal->per_100g_dm':kcal_per_100g_dm/1000,
            'per_100g_dm->per_mj':1/mj_per_100g_dm,
            'per_mj->per_100g_dm':mj_per_100g_dm,
            'per_1000kcal->per_mj':uc['kcal_per_mj']/1000,
            'per_mj->per_1000kcal':1000/uc['kcal_per_mj']}
        self.assertEqual(set(uc['factors']),set(expected))
        for key,value in expected.items():
            self.assertAlmostEqual(uc['factors'][key],value,delta=value*0.002,msg=key)

    def test_corrected_rows_not_swapped(self):
        f=self.cfg['unit_conversion']['factors']
        # 旧表错误：0.598/0.239 互换，1.6736 方向写成每 MJ → 每 1000 kcal
        self.assertEqual(f['per_1000kcal->per_mj'],0.239)
        self.assertEqual(f['per_100g_dm->per_mj'],0.598)
        self.assertEqual(f['per_mj->per_100g_dm'],1.6736)
        self.assertEqual(f['per_mj->per_1000kcal'],4.184)

    def test_round_trips_and_validation(self):
        for a in ('per_100g_dm','per_1000kcal','per_mj'):
            for b in ('per_100g_dm','per_1000kcal','per_mj'):
                back=convert_reference(convert_reference(10,a,b,self.cfg),b,a,self.cfg)
                self.assertAlmostEqual(back,10,delta=0.01,msg=f'{a}->{b}')
        # 锌示例（E5）：10.00 mg/100 g DM = 25 mg/1000 kcal
        self.assertAlmostEqual(convert_reference(10.0,'per_100g_dm','per_1000kcal',self.cfg),25.0)
        self.assertIsNone(convert_reference(None,'per_mj','per_100g_dm',self.cfg))
        with self.assertRaises(ValueError):
            convert_reference(1,'per_kg_as_fed','per_mj',self.cfg)

    def test_vitamin_d_legal_max_is_per_100g_dm(self):
        vd=self.cfg['legal_limits']['vitamin_d']
        self.assertEqual((vd['max'],vd['unit'],vd['basis']),(227,'IU','per_100g_dm'))
        self.assertNotIn('legal_max_iu_per_kg_as_fed',json.dumps(self.cfg))
        self.assertEqual(legal_max('vitamin_d','per_100g_dm',cfg=self.cfg),227)
        self.assertEqual(legal_max('vitamin_d','per_kg_dm',cfg=self.cfg),2270)
        # 含水 12% 时应回到 EU 原值约 2000 IU/kg 原样
        self.assertAlmostEqual(legal_max('vitamin_d','per_kg_as_fed',12,self.cfg),2000,delta=5)
        # 湿粮 80% 水分：约 454 IU/kg 原样，绝不是 227
        self.assertAlmostEqual(legal_max('vitamin_d','per_kg_as_fed',80,self.cfg),454,delta=1)

    def test_vitamin_d_as_fed_needs_declared_moisture(self):
        self.assertIsNone(legal_max('vitamin_d','per_kg_as_fed',None,self.cfg))
        for bad in ('per_kg','iu_per_kg_as_fed',None):
            with self.assertRaises(ValueError):
                legal_max('vitamin_d',bad,cfg=self.cfg)
        with self.assertRaises(ValueError):
            legal_max('vitamin_d','per_kg_as_fed',100,self.cfg)

class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Database(Path(self.tmp.name)/'test.db')
    def tearDown(self):self.tmp.cleanup()

    def test_pipeline_idempotence_dm_wet_and_missing(self):
        p=product();import_products(self.db,[p]);import_products(self.db,[p])
        self.assertEqual(self.db.count(),1)
        a=self.db.products()[0]['analysis']
        self.assertAlmostEqual(a['protein_dm_pct'],36/92*100,places=5)
        wet=product('wet','wet',nutrition='protein 10%; fat 5%; fiber 1%; ash 2%; moisture 80%; phosphorus .2%')
        import_products(self.db,[wet])
        self.assertIsNone(next(p for p in self.db.products() if p['product_type']=='wet')['analysis']['carbs_est_dm_pct'])
        # Separate database/source baseline: missing moisture must remain null.
        with tempfile.TemporaryDirectory() as d:
            db=Database(Path(d)/'a.db');import_products(db,[product(nutrition='protein 30%; fat 10%; fiber 2%; ash 7%')])
            self.assertIsNone(db.products()[0]['analysis']['protein_dm_pct'])

    def test_quality_gate_is_atomic(self):
        import_products(self.db,[product()])
        p=product(nutrition='protein 30%; fat 10%')
        outcome=import_products(self.db,[p])[0]
        self.assertEqual(outcome['status'],'quarantined')
        self.assertEqual(self.db.products()[0]['as_fed']['moisture'],8)
        self.assertEqual(len(self.db.review()['quarantine']),1)
        outcome=import_products(self.db,[product('new')],failed=1)[0]
        self.assertEqual(outcome['status'],'quarantined')
        self.assertEqual(self.db.count(),1)

    def test_failures_with_site_keep_source_baseline(self):
        items=[product(str(i)) for i in range(5)]
        outcome=import_products(self.db,items,failed=1,errors=[{'site':'demo','url':'u','error':'HTTP 503'}])
        self.assertEqual((outcome[0]['status'],outcome[0]['failed']),('committed',1))
        import_products(self.db,items,failed=1,errors=[{'url':'u','error':'unknown'}])
        with self.db.connect() as c:
            self.assertEqual([r[0] for r in c.execute('SELECT source FROM runs ORDER BY id')],['demo','mixed'])

    def test_bad_data_never_analyzed(self):
        p=product(nutrition='protein 50%; fat 30%; fiber 5%; ash 10%; moisture 10%')
        import_products(self.db,[p]);r=self.db.products()[0]
        self.assertTrue(r['data_suspect']);self.assertIsNone(r['analyzed_at'])
        self.assertTrue(all(v is None for v in r['analysis'].values()))

    def test_becoming_excluded_removes_public_record(self):
        p=product();import_products(self.db,[p]);p['product_description_raw']='Complementary food'
        import_products(self.db,[p]);self.assertEqual(self.db.count(),0)

    def test_config_change_reflected_without_frontend_change(self):
        import_products(self.db,[product()]);cfg=config()
        cfg['dimensions']['protein_dm_pct']['bands'][0]['max']=50
        with patch('database.config',return_value=cfg):
            self.assertEqual(self.db.products()[0]['bands']['protein_dm_pct']['label'],'below_minimum')

    def test_reanalysis_and_stats(self):
        import_products(self.db,[product(),product('second')])
        self.assertEqual(self.db.reanalyze('2.2',True),0)
        self.assertEqual(self.db.reanalyze('2.2'),2)
        with self.assertRaises(ValueError):self.db.reanalyze('9.0')
        self.assertEqual(stats(self.db.products())['dry:adult']['protein_dm_pct']['count'],2)

class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.db=Database(Path(cls.tmp.name)/'a.db')
        import_products(cls.db,[product(),product('prescription',prescription=True),product('kitten',stage='kitten')])
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(cls.db,True))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.tmp.cleanup()
    def get(self,path):
        with urlopen(self.base+path) as r:return json.load(r)
    def test_api_search_filters_detail_compare(self):
        data=self.get('/api/products');self.assertEqual(data['total'],2)
        self.assertEqual(self.get('/api/products?prescription=true')['total'],3)
        self.assertEqual(self.get('/api/products?stage=kitten')['total'],1)
        self.assertEqual(self.get('/api/products?q=nonexistent')['total'],0)
        ids=[p['id'] for p in data['items']]
        self.assertIn('snapshot_text',self.get('/api/products/'+ids[0]))
        self.assertEqual(len(self.get('/api/compare?ids='+','.join(ids))['items']),2)
        with self.assertRaises(HTTPError) as error:self.get('/api/products/missing')
        self.assertEqual(error.exception.code,404)
        with self.assertRaises(HTTPError) as error:self.get('/api/compare?ids='+ids[0])
        self.assertEqual(error.exception.code,400)
    def test_static_and_no_file_traversal(self):
        with urlopen(self.base+'/') as r:self.assertIn(b'PawBase',r.read())
        with self.assertRaises(HTTPError):urlopen(self.base+'/../database.py')
        self.assertEqual(self.get('/api/health')['mode'],'demo')
