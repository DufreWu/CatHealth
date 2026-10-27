"""SQLite persistence with atomic imports and an isolated failed-run table."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from nutrition import config, analyze, dimension_config, band_for, context_notes, verdicts


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS products (id TEXT PRIMARY KEY, source TEXT NOT NULL, payload TEXT NOT NULL, fingerprint TEXT NOT NULL, last_seen_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS excluded (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, source TEXT NOT NULL, summary TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS quarantine (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, source TEXT NOT NULL, reason TEXT NOT NULL, payload TEXT NOT NULL);
            ''')

    def connect(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        return c

    def count(self):
        with self.connect() as c:
            return c.execute('SELECT count(*) FROM products').fetchone()[0]

    def import_batch(self, records, excluded, source, failed=0, errors=None):
        cfg = config()
        attempted = len({r['source_product_id'] for r in records}) + len(excluded) + failed
        summary = {'attempted':attempted,'failed':failed,'records':len(records),'excluded':len(excluded),'new':0,'recipe_changed':0,'price_changed':0,'unchanged':0,'needs_review':sum(r['needs_review'] for r in records),'data_suspect':sum(r['data_suspect'] for r in records),'errors':errors or [],'api_cost_eur':0}
        summary['null_rates'] = {k:sum(r['as_fed'].get(k) is None for r in records)/len(records) if records else 0 for k in ('protein','moisture')}
        summary['null_rates']['price'] = sum(not any(o['reference_price'] is not None for o in r['offers']) for r in records)/len(records) if records else 0
        reasons=[]
        if attempted and (attempted-failed)/attempted < .8:
            reasons.append('抓取成功率低于 80%')
        with self.connect() as c:
            previous = c.execute('SELECT summary FROM runs WHERE source=? ORDER BY id DESC LIMIT 1',(source,)).fetchone()
            if previous:
                prior=json.loads(previous[0])
                for k,v in summary['null_rates'].items():
                    if records and prior['records'] and v-prior['null_rates'][k] > .2 + 1e-9:
                        reasons.append(f'{k} 空值率上升超过 20 个百分点')
            if reasons:
                summary.update(status='quarantined', reasons=reasons)
                c.execute('INSERT INTO quarantine(created_at,source,reason,payload) VALUES(?,?,?,?)',(now(),source,encode(summary),encode({'records':records,'excluded':excluded})))
                return summary
            incoming = {}
            for record in records:
                incoming.setdefault((record['source'], record['source_product_id']), set()).add(record['id'])
            for (record_source, product_id), keys in incoming.items():
                for old in c.execute('SELECT id,payload FROM products WHERE source=?', (record_source,)).fetchall():
                    if json.loads(old['payload'])['source_product_id'] == product_id and old['id'] not in keys:
                        c.execute('DELETE FROM products WHERE id=?', (old['id'],))
            for r in records:
                key=r['id']
                fp=hashlib.sha256(encode({k:r[k] for k in ('ingredients_raw','nutrition_raw','as_fed','product_type','life_stage','taurine_declared','declaration_raw','is_prescription')}).encode()).hexdigest()
                previous=c.execute('SELECT fingerprint,payload FROM products WHERE id=?',(key,)).fetchone()
                r['analysis']=analyze(r,cfg)
                r['framework_version']=cfg['framework_version']
                r['analyzed_at']=now() if not (r['needs_review'] or r['data_suspect']) else None
                r['needs_reanalysis']=False
                if previous:
                    old=json.loads(previous['payload'])
                    if previous['fingerprint']!=fp:
                        summary['recipe_changed']+=1
                    elif old['framework_version']==cfg['framework_version']:
                        r['analyzed_at']=old['analyzed_at']
                        summary['unchanged']+=1
                    if old['offers'] != r['offers']:
                        summary['price_changed']+=1
                else:
                    summary['new']+=1
                c.execute('INSERT INTO products VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,fingerprint=excluded.fingerprint,last_seen_at=excluded.last_seen_at',(key,r['source'],encode(r),fp,now()))
                c.execute('DELETE FROM excluded WHERE id=?',(r['source']+':'+r['source_product_id'],))
            for r in excluded:
                c.execute('INSERT OR REPLACE INTO excluded VALUES(?,?)',(r['id'],encode(r)))
                # A product that becomes complementary must disappear from the guide.
                for row in c.execute('SELECT id,payload FROM products WHERE source=?',(r['source'],)).fetchall():
                    if json.loads(row['payload'])['source_product_id']==r['source_product_id']:
                        c.execute('DELETE FROM products WHERE id=?',(row['id'],))
            summary['status']='committed'
            c.execute('INSERT INTO runs(created_at,source,summary) VALUES(?,?,?)',(now(),source,encode(summary)))
        return summary

    def products(self):
        with self.connect() as c:
            rows=c.execute('SELECT payload,last_seen_at FROM products ORDER BY id').fetchall()
        cfg=config()
        result=[]
        for row in rows:
            p=json.loads(row['payload'])
            p['last_seen_at']=row['last_seen_at']
            p['benchmark_dimensions']=dimension_config(cfg,p['life_stage'])
            p['bands']={k:band_for(p['analysis'].get(k),dim) for k,dim in p['benchmark_dimensions'].items()}
            p['context_notes']=context_notes(p,cfg)
            p['verdicts']=verdicts(p,cfg)
            p['stage_note']=cfg['life_stage_groups'][p['life_stage'] or 'unknown']['note']
            result.append(p)
        return result

    def review(self):
        with self.connect() as c:
            excluded=[json.loads(r[0]) for r in c.execute('SELECT payload FROM excluded ORDER BY id')]
            runs=[dict(r) for r in c.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 20')]
            quarantined=[dict(r) for r in c.execute('SELECT id,created_at,source,reason FROM quarantine ORDER BY id DESC LIMIT 20')]
        for r in runs:
            r['summary']=json.loads(r['summary'])
        for r in quarantined:
            r['reason']=json.loads(r['reason'])
        return {'excluded':excluded,'needs_review':[p for p in self.products() if p['needs_review'] or p['data_suspect']], 'runs':runs,'quarantine':quarantined}

    def reanalyze(self, version, outdated_only=False):
        cfg=config()
        if version != cfg['framework_version']:
            raise ValueError('目标版本必须与 benchmarks.json 一致')
        count=0
        with self.connect() as c:
            for row in c.execute('SELECT id,payload FROM products').fetchall():
                r=json.loads(row['payload'])
                if outdated_only and r['framework_version']==version:
                    continue
                r['analysis']=analyze(r,cfg)
                r['analyzed_at']=now() if not (r['needs_review'] or r['data_suspect']) else None
                r['framework_version']=version
                c.execute('UPDATE products SET payload=? WHERE id=?',(encode(r),row['id']))
                count+=1
        return count


def stats(products):
    # Comparison cohort: same food type and life stage, valid and non-prescription.
    result={}
    def percentile(a,q):
        pos=(len(a)-1)*q
        low=int(pos)
        return a[low]+(a[min(low+1,len(a)-1)]-a[low])*(pos-low)
    for p in products:
        if p['needs_review'] or p['data_suspect'] or p['is_prescription']:
            continue
        cohort=p['product_type']+':'+(p['life_stage'] or 'unknown')
        group=result.setdefault(cohort,{})
        for k in p['benchmark_dimensions']:
            value=p['analysis'].get(k)
            if value is not None:
                group.setdefault(k,[]).append(value)
    for group in result.values():
        for key, values in group.items():
            a=sorted(values)
            group[key]={'count':len(a),'min':min(a),'max':max(a),'median':percentile(a,.5),'p25':percentile(a,.25),'p75':percentile(a,.75)}
    return result
