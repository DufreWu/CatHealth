#!/usr/bin/env python3
"""Local same-origin API + static frontend. Python standard library only."""
import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, unquote
from database import Database, stats
from nutrition import config
from pipeline import import_products

ROOT=Path(__file__).resolve().parent


def handler_for(db, demo=False):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, payload, content_type='application/json; charset=utf-8'):
            body=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode() if isinstance(payload,(dict,list)) else payload
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            try:self.route()
            except (ValueError,KeyError) as e:self.respond(400,{'error':str(e)})
            except Exception:
                import traceback
                traceback.print_exc()
                self.respond(500,{'error':'服务读取失败，请检查终端日志'})

        def route(self):
            parsed=urlsplit(self.path);path=unquote(parsed.path);q=parse_qs(parsed.query)
            def arg(k,default=''):return q.get(k,[default])[0]
            if path=='/api/health':return self.respond(200,{'status':'ok','mode':'demo' if demo else 'real','count':db.count()})
            if path=='/api/benchmarks':return self.respond(200,config())
            if path=='/api/review':return self.respond(200,db.review())
            if path in ('/api/products','/api/stats','/api/compare') or path.startswith('/api/products/'):
                products=db.products()
                if path=='/api/stats':return self.respond(200,{'cohorts':stats(products),'note':'同类型、同生命阶段；排除待复核与处方粮；小样本仅供演示'})
                if path.startswith('/api/products/'):
                    key=path[len('/api/products/'):]
                    product=next((p for p in products if p['id']==key),None)
                    return self.respond(200,product) if product else self.respond(404,{'error':'商品不存在'})
                if path=='/api/compare':
                    ids=list(dict.fromkeys(arg('ids').split(',')))
                    if not 2<=len(ids)<=3:raise ValueError('请选择 2–3 个商品对比')
                    selected=[next((p for p in products if p['id']==i),None) for i in ids]
                    if any(p is None for p in selected):return self.respond(404,{'error':'部分商品已不存在'})
                    return self.respond(200,{'items':selected})
                brands=sorted({p['brand'] for p in products if p['brand']})
                term=arg('q').casefold()
                items=[p for p in products if (not term or term in (p['name']+' '+(p['brand'] or '')).casefold()) and (not arg('type') or p['product_type']==arg('type')) and (not arg('stage') or p['life_stage']==arg('stage')) and (not arg('brand') or p['brand']==arg('brand')) and (arg('prescription')=='true' or not p['is_prescription']) and (arg('grain_free')!='true' or p['is_grain_free'] is True)]
                items.sort(key=lambda p:((p['brand'] or '').casefold(),p['name'].casefold(),p['id']))
                page=max(1,int(arg('page','1')));limit=min(100,max(1,int(arg('limit','24'))))
                total=len(items)
                items=items[(page-1)*limit:page*limit]
                # Snapshot/long label text is fetched only on detail and comparison.
                small=[{k:v for k,v in p.items() if k not in ('snapshot_text','ingredients_raw','nutrition_raw','nutrient_evidence')} for p in items]
                return self.respond(200,{'items':small,'total':total,'page':page,'limit':limit,'brands':brands})
            mapping={'/':'index.html','/methodology':'index.html','/review':'index.html','/app.js':'app.js','/style.css':'style.css'}
            if path not in mapping:return self.respond(404,{'error':'页面不存在'})
            f=ROOT/'web'/mapping[path]
            return self.respond(200,f.read_bytes(),(mimetypes.guess_type(str(f))[0] or 'text/plain')+'; charset=utf-8')
    return Handler


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',type=int,default=8000)
    p.add_argument('--host',default='127.0.0.1')
    p.add_argument('--demo',action='store_true',help='使用独立的虚构演示数据，不写入真实商品库')
    p.add_argument('--db',type=Path)
    a=p.parse_args()
    db=Database(a.db or ROOT/'data'/('demo.db' if a.demo else 'pawbase.db'))
    if db.count()==0:
        seed=ROOT/'data'/('demo_products.json' if a.demo else 'bootstrap.json')
        if seed.exists():import_products(db,json.loads(seed.read_text(encoding='utf-8')))
    server=ThreadingHTTPServer((a.host,a.port),handler_for(db,a.demo))
    print(f'PawBase: http://{a.host}:{a.port} | {"虚构演示库" if a.demo else "历史真实数据"}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
