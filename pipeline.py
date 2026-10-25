"""Adapter from the existing crawler output to the Food Guide database."""
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from nutrition import parse_nutrients, classify_declaration, validate
from database import Database


def infer_type(p):
    text=' '.join(p.get('breadcrumbs',[]))+' '+(p.get('source_url') or '')+' '+p.get('product_description_raw','')+' '+str(p.get('product_type',''))
    if re.search(r'freeze.dried|pakastekuivattu|raakaravinto|raw food|cat treat|kissanherkku',text,re.I):
        return 'excluded'
    if re.search(r'kuivaruoka|dry',text,re.I): return 'dry'
    if re.search(r'm[äa]rk[äa]ruoka|wet',text,re.I): return 'wet'
    return None


def standard_offers(p):
    result=[]
    for v in p.get('variants',[]):
        if p['source_site']=='zooplus' and v.get('sku') and not str(v['sku']).startswith(str(p['product_id'])+'.'):
            continue
        price=None
        reason='未找到可确认的标准单件价格'
        for o in v.get('offers',[]):
            text=json.dumps(o,ensure_ascii=False).lower()
            restricted=('validformembertier','saleprice','strikethroughprice','referencequantity','eligiblequantity','subscription','aggregateoffer')
            if any(x in text for x in restricted) or re.search(r'\b\d+\s*[x×]\s*\d+|säästöpakkaus',v.get('name') or '',re.I):
                reason='含促销、会员、单位价或多件条件，首版保守不取参考价'
                continue
            try:
                candidate=float(str(o.get('price')).replace(',','.'))
                if 0 < candidate < 1e6:
                    price=candidate
                    reason=None
                    break
            except (TypeError,ValueError): pass
        result.append({'sku':v.get('sku'),'size':v.get('name') or v.get('size_raw'),'reference_price':price,'currency':'EUR','source':p['source_site'],'fetched_at':p.get('fetched_at'),'note':reason})
    return result


def normalize(p):
    source=p['source_site']
    if source not in ('zooplus','peten','demo'):
        raise ValueError('不支持的数据源')
    pid=str(p['product_id'])
    description=p.get('product_description_raw','')
    ingredients=p.get('ingredient_blocks',[])
    declaration_text=p.get('declaration_raw') or '\n'.join([description]+[b['raw'] for b in ingredients])
    declaration,raw=classify_declaration(declaration_text)
    food_type=infer_type(p)
    reason=declaration if declaration!='complete' else ('excluded_type' if food_type=='excluded' else ('type_not_found' if not food_type else None))
    if reason:
        return [], [{'id':source+':'+pid,'source':source,'source_product_id':pid,'name':p['name'],'source_url':p.get('source_url'),'reason':reason}]
    blocks=p.get('nutrition_blocks',[])
    # Never fan the rendered Zooplus table out across every SKU.
    if any(b['scope']=='rendered_table_variant' for b in blocks):
        scopes=[b for b in blocks if b['scope']=='rendered_table_variant']
    elif any(b['scope']=='variant' for b in blocks):
        scopes=[b for b in blocks if b['scope']=='variant']
    else:
        scopes=[{'scope':'page_description','variant':None,'raw':'\n'.join(b['raw'] for b in blocks)}]
    records=[]
    for block in scopes:
        scope=block.get('variant')
        nutrient_text=block['raw']
        # Page-level analytical prose is also applicable to a rendered table;
        # disagreement must reach review instead of silently selecting one.
        if block['scope']=='rendered_table_variant':
            nutrient_text+='\n'+'\n'.join(b['raw'] for b in blocks if b['scope']=='page_description')
        values,issues,evidence=parse_nutrients(nutrient_text)
        suspect,review,reasons=validate(values,food_type,issues)
        ing='\n'.join(b['raw'] for b in ingredients if b.get('variant') in (None,scope))
        additives='\n'.join(v.get('additives_raw','') for v in p.get('variants',[]) if v.get('sku')==scope)
        matching_variants=[v for v in p.get('variants',[]) if v.get('sku')==scope or (scope and block['scope']=='rendered_table_variant' and re.search(r'(?<![\d.,])'+re.escape(str(scope))+r'(?![\d.,])',v.get('name') or '',re.I))]
        attributes=' '.join(str(v.get('attributes',{})) for v in matching_variants)
        labels=' '.join([p['name'],description,attributes])
        stage_labels=' '.join([p['name'],attributes,description[:250]])
        # Product description + variant attributes, not recommendations elsewhere on page.
        stage=None
        for name,pattern in [('all',r'all life stages|kaikenikäis'),('kitten',r'\bkitten\b|kissanpenn|\bpentu'),('senior',r'\bsenior\b|ikäänty'),('adult',r'\badult\b|aikuis|täysikasvui')]:
            if re.search(pattern,stage_labels,re.I):
                stage=name;break
        taurine_text=ing+' '+additives+' '+description
        taurine=bool(re.search(r'\btauriini\b|\btaurine\b',taurine_text,re.I)) if taurine_text.strip() else None
        key=source+':'+pid+':'+hashlib.sha256((str(scope)+block['scope']).encode()).hexdigest()[:12]
        offers=standard_offers(p)
        if block['scope']=='variant':
            offers=[o for o in offers if o['sku']==scope]
        records.append({'id':key,'source':source,'source_product_id':pid,'name':p['name'],'brand':p.get('brand'),'source_url':p.get('source_url'),'fetched_at':p.get('fetched_at'),'is_demo':source=='demo','product_type':food_type,'life_stage':stage,'is_prescription':bool(re.search(r'veterinary|prescription|dietetic',p['name'],re.I) or re.search(r'veterinary diet|prescription diet|dieettitäysravinto|dieettitäysrehu|eläinlääkärin erikoisruoka',labels,re.I)),'is_grain_free':True if re.search(r'grain.free|viljaton',labels,re.I) else None,'declaration_raw':raw,'ingredients_raw':ing,'nutrition_raw':nutrient_text,'nutrition_scope':{'scope':block['scope'],'variant':scope},'as_fed':values,'nutrient_evidence':evidence,'offers':offers,'taurine_declared':taurine,'snapshot_text':p.get('page_text_snapshot'), 'data_suspect':suspect,'needs_review':review,'review_reasons':reasons,'warnings':p.get('warnings',[])})
    return records,[]


def import_products(db, products, failed=0, errors=None, source_label=None):
    errors=errors or []
    grouped={}
    for p in products:
        grouped.setdefault(p['source_site'],[]).append(p)
    # Failed URLs must be supplied per source; unknown failures guard the whole import.
    # 每条失败都标明 site 时按来源归属，单源批次只与该来源自己的历史比较。
    attributed=len(errors)==failed and all(e.get('site') in ('zooplus','peten','demo') for e in errors)
    if failed and not attributed:
        records,excluded=[],[]
        for p in products:
            a,b=normalize(p);records+=a;excluded+=b
        return [db.import_batch(records,excluded,source_label or 'mixed',failed,errors)]
    for e in errors if failed else []:
        grouped.setdefault(e['site'],[])
    summaries=[]
    for source,items in grouped.items():
        records,excluded=[],[]
        for p in items:
            a,b=normalize(p);records+=a;excluded+=b
        site_errors=[e for e in errors if e['site']==source] if failed else errors
        summaries.append(db.import_batch(records,excluded,source_label or source,len(site_errors) if failed else 0,site_errors))
    return summaries


def hydrate_cache(products, cache):
    """Migrate supplied historical HTML without network access or timestamp changes."""
    from bs4 import BeautifulSoup
    from crawler import schema_nodes, has_type, product_text
    for p in products:
        key=hashlib.sha256(p['source_url'].encode()).hexdigest()
        file=Path(cache)/(key+'.html')
        if not file.exists(): continue
        html=file.read_bytes().decode('utf-8')
        if p.get('source_html_sha256') and hashlib.sha256(html.encode()).hexdigest()!=p['source_html_sha256']:
            raise ValueError('缓存快照校验值与原始记录不匹配')
        soup=BeautifulSoup(html,'html.parser')
        nodes=schema_nodes(soup)
        target=urlsplit(p['source_url']).path
        node=next((n for n in nodes if (has_type(n,'Product') or has_type(n,'ProductGroup')) and (n.get('name')==p['name'] or urlsplit(str(n.get('url',''))).path==target)),{})
        p['product_description_raw']=product_text(soup,p['source_site'],node.get('description',''))
        p['page_text_snapshot']=p['product_description_raw']
    return products
