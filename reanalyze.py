import argparse
from pathlib import Path
from database import Database

if __name__=='__main__':
    p=argparse.ArgumentParser(description='使用保留的标签数据重算确定性营养字段，不调用 AI')
    p.add_argument('--framework',required=True)
    p.add_argument('--outdated-only',action='store_true')
    p.add_argument('--db',type=Path,default=Path(__file__).resolve().parent/'data/pawbase.db')
    a=p.parse_args()
    print({'reanalyzed':Database(a.db).reanalyze(a.framework,a.outdated_only)})
