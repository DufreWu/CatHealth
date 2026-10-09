#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from database import Database
from pipeline import import_products, hydrate_cache

ROOT=Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser(description='Food Guide 本地数据管理（无需 API 密钥）')
    parser.add_argument('--db',type=Path,default=ROOT/'data/pawbase.db')
    sub=parser.add_subparsers(dest='command',required=True)
    imp=sub.add_parser('import');imp.add_argument('file',type=Path);imp.add_argument('--cache',type=Path);imp.add_argument('--errors',type=Path)
    sub.add_parser('review')
    exp=sub.add_parser('export');exp.add_argument('file',type=Path)
    args=parser.parse_args();db=Database(args.db)
    if args.command=='import':
        products=json.loads(args.file.read_text(encoding='utf-8'))
        if args.cache:products=hydrate_cache(products,args.cache)
        errors=json.loads(args.errors.read_text(encoding='utf-8')) if args.errors else []
        summaries=import_products(db,products,len(errors),errors)
        print(json.dumps(summaries,ensure_ascii=False,indent=2))
        return 2 if any(s['status']=='quarantined' for s in summaries) else 0
    if args.command=='review':print(json.dumps(db.review(),ensure_ascii=False,indent=2))
    if args.command=='export':args.file.write_text(json.dumps(db.products(),ensure_ascii=False,indent=2),encoding='utf-8')
    return 0

if __name__=='__main__':raise SystemExit(main())
