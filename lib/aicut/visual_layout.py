"""Conservative title placement in the renderer's 1080x1920 coordinate space."""
import math

def overlaps(a,b):
    return a[0]<b[2] and b[0]<a[2] and a[1]<b[3] and b[1]<a[3]

def choose_title_y(regions):
    for y in (145,100,1010,1150):
        box=(60,y,1000,y+210)
        if not any(overlaps(box,r) for r in regions):return y
    return None

def prepare_cards(cards,clips,resolve,manual_regions=()):
    import cv2
    from pathlib import Path
    model=Path(__file__).resolve().parents[2]/'models/yunet/face_detection_yunet_2023mar.onnx'
    if not model.is_file():raise ValueError('YuNet face detector model unavailable')
    classifier=cv2.FaceDetectorYN.create(str(model),'',(360,640),.75,.3,5000)
    offsets=[];offset=0.
    for c in clips:
        length=(c['end']-c['start'])/c.get('speed',1)
        offsets.append((offset,offset+length,c));offset+=length
    output=[];audit=[]
    for card in cards:
        regions=[tuple(r) for r in manual_regions]
        sampled=0;failed=0
        # Sparse checks are evidence of these frames only, not continuous tracking.
        for t in (card['start'],(card['start']+card['end'])/2,max(card['start'],card['end']-.05)):
            match=next(((a,c) for a,b,c in offsets if a<=t<b),None)
            if not match:failed+=1;continue
            a,c=match;capture=cv2.VideoCapture(str(resolve(c['file'])))
            capture.set(cv2.CAP_PROP_POS_MSEC,(c['start']+(t-a)*c.get('speed',1))*1000)
            ok,frame=capture.read();capture.release()
            if not ok:failed+=1;continue
            _,detected=classifier.detect(cv2.resize(frame,(360,640)))
            faces=[] if detected is None else detected[:,:4]
            sampled+=1
            for x,y,w,h in faces:
                regions.append((max(0,int(x*3)-35),max(0,int(y*3)-45),min(1080,int((x+w)*3)+35),min(1920,int((y+h)*3)+45)))
        y=choose_title_y(regions) if not failed else None
        audit.append({'start':card['start'],'sampled_frames':sampled,'failed_frames':failed,'regions':regions,'selected_y':y,
                      'basis':'sampled_yunet_face_detection_plus_manual_regions','limitations':'no hand or product detector; no continuous tracking'})
        if y is not None:output.append({**card,'layout_y':y})
    return output,audit
