"""Subpixel video camera and composited motion. Outputs video, never edits image assets."""
import json, math, subprocess
from pathlib import Path
import cv2
import numpy as np
ROOT=Path(__file__).resolve().parent
FPS=30; W,H=720,1280
def render_shots():
    master=cv2.imread(str(ROOT/'assets/coffee-cinema-master.png'))
    if master is None: raise ValueError('Missing master')
    panels=np.array_split(master,3,axis=1)
    yy,xx=np.mgrid[0:H,0:W].astype(np.float32)
    trajectories=[]
    for index,panel in enumerate(panels):
        base=cv2.resize(panel,(W,H),interpolation=cv2.INTER_CUBIC)
        cmd=['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','bgr24','-s',f'{W}x{H}','-r',str(FPS),'-i','-','-an','-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p',str(ROOT/f'media-build/shot-{index}.mp4')]
        proc=subprocess.Popen(cmd,stdin=subprocess.PIPE)
        positions=[]
        for n in range(150):
            t=n/FPS; u=n/149; ease=u*u*(3-2*u)
            z=1.015+.105*ease
            dx=(index-1)*5*ease; dy=-5*ease
            mat=np.float32([[z,0,(1-z)*W/2+dx],[0,z,(1-z)*H/2+dy]])
            frame=cv2.warpAffine(base,mat,(W,H),flags=cv2.INTER_CUBIC,borderMode=cv2.BORDER_REFLECT_101)
            if index in (1,2):
                # Distort the smoke region gently, never the cup/dripper silhouette.
                center=370 if index==2 else 290
                upper=np.exp(-((xx-center)/135)**2)*np.clip((690-yy)/190,0,1)*np.clip((yy-110)/120,0,1)
                mx=xx+np.sin(yy*.014+t*1.2)*6*upper
                my=yy+np.sin(xx*.016-t*.9)*3*upper
                frame=cv2.remap(frame,mx.astype(np.float32),my.astype(np.float32),cv2.INTER_CUBIC,borderMode=cv2.BORDER_REFLECT_101)
                steam=np.zeros((H,W),np.float32)
                for k in range(3):
                    bottom=690 if index==2 else 550
                    for step in range(160):
                        h=step*2.8; y=bottom-h
                        x=center+math.sin(h*.018-t*1.7+k*1.5)*(10+h*.045)+(k-1)*19
                        strength=.15*math.sin(math.pi*step/160)**1.2*(.65+.35*math.sin(t*1.6+k))
                        cv2.circle(steam,(int(x),int(y)),6+int(h*.015),strength,-1)
                steam=cv2.GaussianBlur(steam,(0,0),8)
                frame=np.clip(frame.astype(np.float32)+steam[:,:,None]*np.array([90,110,130],np.float32),0,255).astype(np.uint8)
            if index==1:
                # Light travels along the photographed water stream, implying flow.
                glow=np.zeros((H,W),np.float32)
                for k in range(5):
                    q=(t*.75+k/5)%1
                    x=359-51*q; y=198+295*q
                    cv2.ellipse(glow,(int(x),int(y)),(2,10),-10,0,360,.10,-1)
                glow=cv2.GaussianBlur(glow,(0,0),2)
                frame=np.clip(frame.astype(np.float32)+glow[:,:,None]*145,0,255).astype(np.uint8)
            proc.stdin.write(frame.tobytes())
            positions.append({'frame':n,'zoom':z,'dx':dx,'dy':dy})
        proc.stdin.close()
        if proc.wait(): raise RuntimeError('Motion video encoding failed')
        velocity=np.diff([p['zoom'] for p in positions])
        trajectories.append({'shot':index,'frames':150,'effects':['subpixel_eased_camera']+(['animated_steam'] if index>0 else [])+(['flow_highlights'] if index==1 else []),'max_zoom_acceleration':float(np.max(np.abs(np.diff(velocity)))),'camera_positions':positions})
    (ROOT/'media-build/motion-traces.json').write_text(json.dumps(trajectories,indent=2),encoding='utf-8')
    print('3 motion shots rendered at 30fps with continuous subpixel camera',flush=True)
