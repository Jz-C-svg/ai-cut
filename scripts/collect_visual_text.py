"""Local OCR and visual layout measurements over collected production frames."""
import argparse,hashlib,json,sys,time
from pathlib import Path
import cv2,numpy as np
from collect_local_dataset import save
ROOT=Path(__file__).resolve().parents[1]

def layout(box,w,h):
    p=np.array(box,dtype=float);x0,y0=p.min(axis=0);x1,y1=p.max(axis=0)
    return {'bbox_xyxy':[float(x0),float(y0),float(x1),float(y1)],'bbox_normalized':[float(x0/w),float(y0/h),float(x1/w),float(y1/h)],'relative_height':float((y1-y0)/h),'vertical_zone':'upper' if (y0+y1)/2<h/3 else 'lower' if (y0+y1)/2>h*2/3 else 'middle'}

def inspect_frame(f,ocr,face_detector):
    im=cv2.imdecode(np.fromfile(f['file'],dtype=np.uint8),cv2.IMREAD_COLOR);h,w=im.shape[:2]
    predictions,_=ocr(im);blocks=[]
    gray=cv2.cvtColor(im,cv2.COLOR_BGR2GRAY)
    faces=[{'bbox_xyxy':[int(x),int(y),int(x+fw),int(y+fh)],'center_x_ratio':float((x+fw/2)/w),'center_y_ratio':float((y+fh/2)/h)} for x,y,fw,fh in face_detector.detectMultiScale(gray,scaleFactor=1.15,minNeighbors=5,minSize=(32,32))] if face_detector is not None else []
    for box,text,score in predictions or []:
        region=layout(box,w,h);x0,y0,x1,y1=[int(v) for v in region['bbox_xyxy']]
        patch=im[max(0,y0):min(h,y1),max(0,x0):min(w,x1)]
        colors=[]
        if patch.size:
            rgb=cv2.cvtColor(patch,cv2.COLOR_BGR2RGB);quant=(rgb//32)*32+16;unique,counts=np.unique(quant.reshape(-1,3),axis=0,return_counts=True)
            colors=[{'rgb':c.tolist(),'fraction':float(n/counts.sum())} for c,n in sorted(zip(unique,counts),key=lambda x:-x[1])[:4]]
        overlaps=[]
        for i,face in enumerate(faces):
            a,b,c,d=face['bbox_xyxy'];area=max(0,min(x1,c)-max(x0,a))*max(0,min(y1,d)-max(y0,b))
            if area>0:overlaps.append(i)
        blocks.append({'text':str(text),'confidence':float(score),'polygon':box,**region,'large_text_candidate':region['relative_height']>=.045,'region_palette_includes_background':colors,'face_candidate_overlap_indices':overlaps,'type':'screen_text_unverified_overlay_or_physical_sign'})
    return {'frame':f['file'],'frame_sha256':f['sha256'],'time':f['time'],'width':w,'height':h,'text_blocks':blocks,'face_detector_candidates':faces,'face_detection_status':'available' if face_detector is not None else 'not_run_no_detector_in_installed_opencv','speech_context':f['speech_context'],'review_status':'automatic_unreviewed'}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--runtime',default=str(ROOT/'work/dataset-ocr-runtime'));ap.add_argument('--wait',action='store_true');args=ap.parse_args()
    sys.path.insert(0,args.runtime);from rapidocr_onnxruntime import RapidOCR
    ocr=RapidOCR(det_model_path=None,det_limit_side_len=640,det_limit_type='max',use_angle_cls=False)
    face=cv2.CascadeClassifier(cv2.data.haarcascades+'haarcascade_frontalface_default.xml') if hasattr(cv2,'CascadeClassifier') and hasattr(cv2,'data') else None
    dataset=Path(args.dataset);out=dataset/'production-visual'
    rows=[r for r in json.loads((dataset/'manifest.json').read_text(encoding='utf-8')) if r['kind']=='video' and not r.get('duplicate_of')]
    pending={r['sha256']:r for r in rows};completed=[];errors=[];started=time.time()
    while pending:
        progressed=False
        for sha,row in list(pending.items()):
            dest=out/'videos'/sha;p=dest/'production.json';target=dest/'visual-text.json'
            if not p.exists():continue
            try:
                production=json.loads(p.read_text(encoding='utf-8'))
                if target.exists():result=json.loads(target.read_text(encoding='utf-8'))
                else:
                    frames=[inspect_frame(f,ocr,face) for f in production['frames']]
                    result={'source':row['file'],'source_sha256':sha,'ocr_model':'RapidOCR 1.2.3 / PP-OCRv3 ONNX CPU','detector_max_side':640,'frames':frames,'text_blocks':sum(len(f['text_blocks']) for f in frames),'limits':['OCR text is unverified; confidence is a model score, not calibrated correctness.','Sampled frames cannot recover every caption or animated title; screen text may belong to signs rather than added overlays.','Face boxes are detector candidates, not identities or confirmed subject framing.','Region palette includes background, shadows and outlines; it is not an exact font-color identification.']}
                    save(target,result)
                completed.append({'source':row['file'],'sha256':sha,'visual_text':str(target),'frames':len(result['frames']),'text_blocks':result['text_blocks']});print(f'{len(completed)}/{len(rows)} OCR frames={len(result["frames"])} blocks={result["text_blocks"]} {row["relative"]}',flush=True)
            except Exception as e:errors.append({'source':row['file'],'error':str(e)});print('ERROR '+str(e),flush=True)
            del pending[sha];progressed=True;save(out/'visual-text-index.json',completed);save(out/'visual-text-errors.json',errors)
        if not pending:break
        if not args.wait or time.time()-started>5400:break
        if not progressed:time.sleep(3)
    models={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(args.runtime)/'rapidocr_onnxruntime/models').glob('*.onnx')}
    save(out/'visual-text-summary.json',{'videos':len(completed),'expected_videos':len(rows),'frames':sum(r['frames'] for r in completed),'text_blocks':sum(r['text_blocks'] for r in completed),'errors':errors,'pending_sources':[r['file'] for r in pending.values()],'model_hashes':models,'processing':'local CPU; no uploads'})
    if errors or pending:raise SystemExit(1)

if __name__=='__main__':main()
