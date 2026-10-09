"""Deterministic label analysis. No LLM, estimated moisture, scores or diagnoses."""
import copy
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORE = ('protein', 'fat', 'fiber', 'ash', 'moisture')
LABELS = {
 'protein':r'raakaproteiini|raakavalkuainen|proteiini|crude protein|protein',
 'fat':r'raakarasva|rasvapitoisuus|crude fat|fat',
 'fiber':r'raakakuitu|crude fib(?:er|re)|fib(?:er|re)',
 'ash':r'raakatuhka|tuhka|crude ash|ash',
 'moisture':r'kosteus|moisture', 'calcium':r'kalsium|calcium',
 'phosphorus':r'fosfori|phosphorus', 'magnesium':r'magnesium', 'taurine':r'tauriini|taurine'}


def config():
    return json.loads((ROOT/'config/benchmarks.json').read_text(encoding='utf-8'))


def parse_nutrients(text):
    values, issues, evidence = {}, [], []
    for key, label in LABELS.items():
        matches = list(re.finditer(rf'(?<!\w)(?:{label})(?!\w)\s*[:：]?\s*(?P<q>≥|≤|>=|<=|>|<|min\.?|max\.?|vähintään|enintään)?\s*(?P<n>-?\d+(?:[.,]\d+)?)\s*(?P<u>mg\s*/\s*kg|g\s*/\s*100\s*g|g\s*/\s*kg|%)(?!\w)', text, re.I))
        nums = []
        for m in matches:
            n = float(m['n'].replace(',', '.'))
            unit = re.sub(r'\s+', '', m['u'].lower())
            n *= {'%':1, 'g/100g':1, 'g/kg':.1, 'mg/kg':.0001}[unit]
            evidence.append({'nutrient':key,'raw':m[0],'pct':n,'qualifier':m['q']})
            if m['q']:
                issues.append(f'{key}: 标签为上下限，未作为精确值分析')
            else:
                nums.append(n)
        distinct = {round(x, 8) for x in nums}
        if len(distinct) > 1:
            issues.append(f'{key}: 同一适用范围存在冲突值')
        values[key] = nums[0] if len(distinct) == 1 and not any(m['q'] for m in matches) else None
    return values, issues, evidence


def classify_declaration(text):
    cfg = json.loads((ROOT/'config/declarations.json').read_text(encoding='utf-8'))
    for kind in ('complementary', 'complete'):
        for word in cfg[kind]:
            m = re.search(r'(?<!\w)'+re.escape(word)+r'(?!\w)', text, re.I)
            if m:
                return kind, text[max(0,m.start()-70):m.end()+100].strip()
    return 'declaration_not_found', None


def validate(values, food_type, issues=()):
    reasons = list(issues)
    suspect = any(v is not None and (not math.isfinite(v) or v < 0 or v > 100) for v in values.values())
    known = [values.get(k) for k in CORE if values.get(k) is not None]
    if sum(known) > 100 + 1e-8:
        suspect = True
        reasons.append('已知五项分析成分加和超过 100%')
    if suspect:
        reasons.append('标签数值异常，禁止进入分析')
    limits = {'protein':(4,50), 'fat':(1,30), 'moisture':(3,12) if food_type=='dry' else (70,86)}
    for k, (lo, hi) in limits.items():
        if values.get(k) is not None and not lo <= values[k] <= hi:
            reasons.append(f'{k}: 超出入库复核范围 {lo}–{hi}%')
    return suspect, bool(reasons), reasons


def analyze(record, cfg=None):
    cfg = cfg or config()
    v = record['as_fed']
    out = {k:None for k in ('protein_dm_pct','fat_dm_pct','fiber_dm_pct','ash_dm_pct','phosphorus_dm_pct','calcium_dm_pct','magnesium_dm_pct','carbs_est_dm_pct','ca_p_ratio')}
    if record['data_suspect'] or record['needs_review']:
        return out
    moisture = v.get('moisture')
    if moisture is not None and 0 <= moisture < 100:
        for nutrient in ('protein','fat','fiber','ash','phosphorus','calcium','magnesium'):
            if v.get(nutrient) is not None:
                out[nutrient+'_dm_pct'] = round(v[nutrient]/(100-moisture)*100, 6)
        if record['product_type']=='dry' and all(v.get(k) is not None for k in CORE):
            out['carbs_est_dm_pct'] = round((100-sum(v[k] for k in CORE))/(100-moisture)*100, 6)
    if v.get('calcium') is not None and v.get('phosphorus') is not None and v['phosphorus'] > 0:
        out['ca_p_ratio'] = round(v['calcium']/v['phosphorus'], 6)
    return out


def dimension_config(cfg, stage):
    dims = copy.deepcopy(cfg['dimensions'])
    if stage == 'kitten':
        minimum = cfg['life_stage_groups']['kitten']['protein_minimum']
        dims['protein_dm_pct']['bands'][0]['max'] = minimum
        dims['protein_dm_pct']['fediaf_lines'] = [{'value':minimum,'label':f'幼猫最低 {minimum}%','basis':'产品需求引用 FEDIAF 2025 growth；待原文核对'}]
    return dims


def band_for(value, dim):
    if value is None:
        return None
    if 'normal_range' in dim:
        r = dim['normal_range']
        return {'label':'within' if r['min']<=value<=r['max'] else 'outside', 'name':'常见区间内' if r['min']<=value<=r['max'] else '常见区间外'}
    for band in dim.get('bands', []):
        if 'max' not in band or value < band['max'] or (band.get('inclusive') and value == band['max']):
            return band
    return None


def context_notes(record, cfg):
    notes = []
    for rule in cfg['context_rules']:
        value = record.get(rule['field']) if rule['field']=='taurine_declared' else record['analysis'].get(rule['field'])
        if value is None:
            continue
        op = rule['op']
        hit = (op=='eq' and value==rule['value']) or (op=='gt' and value>rule['value']) or (op=='outside' and not rule['min']<=value<=rule['max'])
        if hit:
            notes.append(rule['text'])
    return notes
