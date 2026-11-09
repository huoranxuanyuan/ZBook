# SPDX-License-Identifier: AGPL-3.0-only
"""Build a clean Windows x64 release from an explicit publication allowlist."""
from pathlib import Path
import argparse,datetime,hashlib,importlib.metadata as metadata,json,os,shutil,subprocess,sys,zipfile
from branding import VERSION,application_icon
ROOT=Path(__file__).resolve().parent
SOURCE_FILES=['app.py', 'batch.py', 'batch_limits.py', 'branding.py', 'build_release.py', 'cache_maintenance.py', 'cli.py', 'core.py', 'cover_browser.py', 'cover_design.py', 'cover_sources.py', 'covers.py', 'epub_export.py', 'input_sources.py', 'layout_widgets.py', 'outline.py', 'paths.py', 'portable_smoke.py', 'preparation.py', 'preview_cache.py', 'reading_style.py', 'safe_image.py', 'safe_network.py', 'safe_regex.py', 'settings_dialog.py', 'smooth_tree.py', 'test_bulk_workflows.py', 'test_cache_maintenance.py', 'test_cover_search_states.py', 'test_cover_sources.py', 'test_cover_workspace.py', 'test_covers_hd.py', 'test_current_book_follow.py', 'test_directory_workspace.py', 'test_input_formats.py', 'test_modern_workspace.py', 'test_progress_flow.py', 'test_queue_actions.py', 'test_reading_layout.py', 'test_remaining_requirements.py', 'test_security_regressions.py', 'test_native_file_drop.py', 'ui_layout.py', 'ui_widgets.py']
DOCS=['.gitattributes','AGENTS.md','README.md','PRIVACY.md','THIRD_PARTY_NOTICES.md','LICENSE','CHANGELOG.md','BUILDING.md','requirements.txt','requirements-build.txt','.gitignore']

def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def source_files():
    paths=[ROOT/name for name in SOURCE_FILES+DOCS]
    paths += [ROOT/'engines/kindling'/name for name in ('kindling.exe','origin.json')]
    fixtures=['encrypted.pdf','legacy_rtf.doc','pdf_page_preview.png','scanned.pdf','text_novel.pdf','upstream-sources.json','upstream_SampleDoc.doc','upstream_simple-table.doc','upstream_simple.doc','word_headings.docx']
    paths += [ROOT/'tests/fixtures'/name for name in fixtures]
    paths += [p for p in (ROOT/'licenses').rglob('*') if p.is_file()]
    for path in paths:
        if not path.is_file() or path.is_symlink():raise ValueError('Missing or invalid release input: '+str(path))
    return sorted(paths)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist-dir',type=Path,default=ROOT.parent/'releases'/VERSION)
    parser.add_argument('--work-dir',type=Path)
    args=parser.parse_args();release=args.dist_dir.resolve()
    if release.exists() and any(release.iterdir()):raise SystemExit('Release directory must be empty; existing releases are never overwritten.')
    release.mkdir(parents=True,exist_ok=True)
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    work=args.work_dir or ROOT.parent/'maintenance'/('build-'+VERSION+'-'+stamp)
    work=work.resolve();work.mkdir(parents=True,exist_ok=False)
    inputs=source_files()
    source_manifest={p.relative_to(ROOT).as_posix():digest(p) for p in inputs}
    engine=ROOT/'engines/kindling/kindling.exe'
    origin=json.loads(engine.with_name('origin.json').read_text(encoding='utf-8'))
    if digest(engine)!=origin['sha256']:raise ValueError('Encoder integrity check failed')
    icon=work/'zbook.ico';application_icon().save(icon,sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
    version_tuple=tuple(map(int,VERSION.split('.')))+(0,)
    version_file=work/'version-info.txt'
    version_file.write_text(f"VSVersionInfo(ffi=FixedFileInfo(filevers={version_tuple!r},prodvers={version_tuple!r},mask=0x3f,flags=0,OS=0x40004,fileType=1,subtype=0,date=(0,0)),kids=[StringFileInfo([StringTable('080404b0',[StringStruct('FileDescription','ZBook 小说转换器'),StringStruct('FileVersion',{VERSION!r}),StringStruct('ProductName','ZBook 小说转换器'),StringStruct('ProductVersion',{VERSION!r})])]),VarFileInfo([VarStruct('Translation',[2052,1200])])])",encoding='utf-8')
    portable=release/('ZBook-'+VERSION+'-Windows-x64');portable.mkdir()
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--onefile','--windowed','--noupx','--name','ZBook','--icon',str(icon),'--paths',str(ROOT),'--distpath',str(portable),'--workpath',str(work/'work'),'--specpath',str(work),'--add-binary',str(engine)+';engines/kindling','--version-file',str(version_file)]
    for excluded in ['PySide6','PyQt6','PyQt5','numpy','pandas','matplotlib','scipy','cv2','IPython','pytest','notebook','tkinter.test','PIL.AvifImagePlugin','PIL._avif']:
        command+=['--exclude-module',excluded]
    packages=['Pillow','requests','urllib3','certifi','idna','charset-normalizer','PyMuPDF','olefile','striprtf','regex','PySocks']
    for package in packages:command+=['--copy-metadata',package]
    command.append(str(ROOT/'app.py'))
    env=os.environ.copy();env['TMP']=env['TEMP']=str(work);env['ZBOOK_DATA_DIR']=str(work/'runtime');env['PYTHONHASHSEED']='0'
    with (work/'build.log').open('w',encoding='utf-8') as log:
        result=subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
    if result.returncode:raise RuntimeError((work/'build.log').read_text(encoding='utf-8')[-5000:])
    exe=portable/'ZBook.exe'
    if exe.stat().st_size>50_000_000:raise ValueError('Portable executable exceeded the 50 MB size budget')
    from PyInstaller.archive.readers import CArchiveReader
    reader=CArchiveReader(str(exe));entries=list(reader.toc)
    if any(any(word in name.lower() for word in ('pyside','qt6core','webengine','calibre','kindlegen.exe','_avif')) for name in entries):raise ValueError('Unexpected bundled engine or component')
    manifest={'version':VERSION,'built_utc':stamp,'python':sys.version.split()[0],'dependencies':{name:metadata.version(name) for name in packages},'encoder':origin,'executable':{'file':'ZBook.exe','bytes':exe.stat().st_size,'sha256':digest(exe)},'source_manifest':source_manifest,'bundled_entries':entries}
    for name in ('README.md','PRIVACY.md','THIRD_PARTY_NOTICES.md','LICENSE','CHANGELOG.md'):shutil.copy2(ROOT/name,portable/name)
    shutil.copytree(ROOT/'licenses',portable/'licenses')
    (portable/'release-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    with zipfile.ZipFile(release/('ZBook-'+VERSION+'-source.zip'),'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for path in inputs:archive.write(path,'ZBook-source/'+path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(release/('ZBook-'+VERSION+'-Windows-x64.zip'),'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for path in portable.rglob('*'):
            if path.is_file():archive.write(path,portable.name+'/'+path.relative_to(portable).as_posix())
    print(json.dumps({'portable':str(portable),'exe':manifest['executable'],'work':str(work)},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
