# SPDX-License-Identifier: AGPL-3.0-only
"""Verification route used to test a frozen build without installed Python/Calibre."""
import sys,json,os,ssl,pyexpat,importlib.metadata as metadata
from pathlib import Path
from core import new_job
from batch import find_converter,Options,run_batch
from paths import ROOT,APP_ROOT
def main():
    args=sys.argv[sys.argv.index('--smoke-test')+1:]
    if len(args) not in (2,3):raise ValueError('--smoke-test requires input.txt and output directory')
    source,out=args[:2];job=new_job(source);network=[];selected=None
    if '--network' in args:
        from covers import search_details
        found=search_details(job.title,job.author,True)
        if not found.covers:raise RuntimeError('打包版未能获取封面')
        job.cover_path=found.covers[0].path;job.cover_source=found.covers[0].provider
        from dataclasses import asdict
        selected=asdict(found.covers[0]);network=found.diagnostics
    report=run_batch([job],Options(out,('epub','mobi')))
    result={'app_root':str(APP_ROOT),'data_root':str(ROOT),'frozen':bool(getattr(sys,'frozen',False)),'engine':find_converter(),'network':network,'selected_cover':selected,'report':report,'runtime':{'python':sys.version,'openssl':ssl.OPENSSL_VERSION,'expat':pyexpat.EXPAT_VERSION,'dependencies':{name:metadata.version(name) for name in ('Pillow','requests','urllib3','certifi','idna','charset-normalizer','PyMuPDF','olefile','striprtf','regex','PySocks')}}}
    target=Path(out);target.mkdir(parents=True,exist_ok=True)
    (target/'portable-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    if any(r['status']!='完成' for r in report['results']):raise RuntimeError(str(result))
if __name__=='__main__':main()
