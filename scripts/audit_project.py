"""Offline project audit with explicit isolated test allowlist; no editor or model calls."""
import ast,concurrent.futures,json,os,subprocess,sys,time,argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TESTS=['selftest_production.py','selftest_professional_editing.py','selftest_complete_editing.py','selftest_learning_context.py','selftest_rag_core.py','selftest_local_dataset.py','selftest_production_visual.py','selftest_kinetic_text_dataset.py','selftest_grounded_flower_plan.py','selftest_app_security.py','selftest_video_understanding.py','selftest_platform_rag.py','selftest_resilience.py','selftest_router.py','selftest_bridges.py','selftest_export_kinetic_text.py','selftest_kinetic_text_renderer.py','selftest_kinetic_text_agent.py','selftest_effect_decision_dataset.py','selftest_audio_event_dataset.py','selftest_account_export_review.py']
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',required=True);args=ap.parse_args()
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    files=[]
    for folder in ('scripts','lib/aicut','gui','app','ai_editor_core'):
        files.extend(p for p in (ROOT/folder).rglob('*.py') if not {'venv','node_modules','__pycache__'}&set(p.parts))
    files.extend(ROOT.glob('*.py'))
    failures=[]
    for p in files:
        try:ast.parse(p.read_text(encoding='utf-8-sig'),filename=str(p))
        except (SyntaxError,UnicodeError) as ex:failures.append({'file':str(p.relative_to(ROOT)),'error':str(ex)})
    def run(name):
        started=time.monotonic()
        try:
            python=os.environ.get('AICUT_PYTHON',sys.executable)
            p=subprocess.run([python,str(ROOT/'scripts'/name)],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180,env={**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONUTF8':'1'})
            text=p.stdout+p.stderr;(out/(name+'.log')).write_text(text,encoding='utf-8')
            return {'script':name,'returncode':p.returncode,'seconds':round(time.monotonic()-started,2),'log':str(out/(name+'.log'))}
        except subprocess.TimeoutExpired:return {'script':name,'returncode':None,'error':'timeout'}
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for r in pool.map(run,TESTS):results.append(r);print(json.dumps(r),flush=True)
    summary={'python_files_parsed':len(files),'syntax_errors':failures,'tests':results,'all_passed':not failures and all(r['returncode']==0 for r in results),'scope':'explicit offline regression allowlist; no UI, cloud, paid API or model calls'}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    return 0 if summary['all_passed'] else 1
if __name__=='__main__':raise SystemExit(main())
