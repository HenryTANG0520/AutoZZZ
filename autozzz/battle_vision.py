"""Geometry-bound visual controls for training, separate from daily policy."""
import re
from .models import normalize,Token


def local_start_button(scene,vision):
    """Native-resolution text fallback; never treats 出线 or coordinates as 出战."""
    return local_button(scene,vision,"出战")


def local_button(scene,vision,label):
    matches=[t for t in regional_tokens(scene,vision,(.84,.91,.97,.99))
             if normalize(t.text)==normalize(label) and t.score>=.6]
    return matches[0] if len(matches)==1 else None


def regional_tokens(scene,vision,area):
    if scene.image is None:return []
    h,w=scene.image.shape[:2]
    x1,y1,x2,y2=[round(value*(w if i%2==0 else h)) for i,value in enumerate(area)]
    sample=vision.read(scene.image[y1:y2,x1:x2])
    result=[]
    for t in sample.tokens:
        a,b,c,d=t.box
        result.append(Token(t.text,t.score,((x1+a*(x2-x1))/w,(y1+b*(y2-y1))/h,
                                           (x1+c*(x2-x1))/w,(y1+d*(y2-y1))/h)))
    return result


def energy_readout(scene):
    values=[]
    for t in scene.tokens:
        if t.within((.72,.91,.89,.99)):
            m=re.fullmatch(r"(\d{1,4})[/／](\d{1,3})",normalize(t.text))
            if m: values.append(tuple(map(int,m.groups())))
    return values[0] if len(values)==1 and 0<values[0][1]<=100 else None


def selected_stage(scene, name):
    import cv2
    import numpy as np
    targets=[t for t in scene.tokens if normalize(t.text)==normalize(name) and t.within((.03,.63,.95,.84))]
    if len(targets)!=1 or scene.image is None:return False
    h,w=scene.image.shape[:2]
    title=targets[0]
    x,y=title.box[0],title.center[1]
    x1,x2=max(0,int((x-.027)*w)),min(w,int((x+.170)*w))
    y1,y2=max(0,int((y-.057)*h)),min(h,int((y+.125)*h))
    patch=scene.image[y1:y2,x1:x2]
    if not patch.size:return False
    b,g,r=[patch[:,:,i].astype(int) for i in range(3)]
    mask=((g>150)&(r>85)&(b<100)&(g>=r*.75)).astype('uint8')
    count,labels,stats,centers=cv2.connectedComponentsWithStats(mask)
    return any(cw>.14*w and ch>.10*h for xx,yy,cw,ch,size in stats[1:])


def horizontal_arrow(scene, direction):
    import cv2
    import numpy as np
    if scene.image is None:return None
    h,w=scene.image.shape[:2]
    area=(.935,.69,.969,.80) if direction=='right' else (.031,.69,.065,.80)
    x1,y1,x2,y2=[round(v*(w if i%2==0 else h)) for i,v in enumerate(area)]
    crop=scene.image[y1:y2,x1:x2]
    mask=(np.min(crop[:,:,:3],axis=2)>215).astype('uint8')
    count,labels,stats,centers=cv2.connectedComponentsWithStats(mask)
    candidates=[]
    for i in range(1,count):
        x,y,cw,ch,pixels=stats[i]
        if .012*h<ch<.065*h and .003*w<cw<.025*w and pixels>.000025*w*h:
            candidates.append(((x1+centers[i][0])/w,(y1+centers[i][1])/h))
    return candidates[0] if len(candidates)==1 else None


def enemy_slots(scene):
    import cv2
    import numpy as np
    if scene.image is None:return []
    h,w=scene.image.shape[:2]
    x1,x2,y1,y2=round(.12*w),round(.25*w),round(.10*h),round(.88*h)
    patch=scene.image[y1:y2,x1:x2]
    b,g,r=[patch[:,:,i].astype(int) for i in range(3)]
    mask=((r>140)&(g>100)&(b<180)&(r-b>35)&(g-b>20)).astype('uint8')
    mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    slots=[]
    for contour in contours:
        x,y,cw,ch=cv2.boundingRect(contour)
        if .065*w<cw<.12*w and .07*h<ch<.15*h:
            slots.append(((x1+x+cw/2)/w,(y1+y+ch/2)/h))
    return sorted(slots,key=lambda v:v[1])


def result_energy(scene):
    values=[]
    for t in scene.tokens:
        if t.within((.50,.91,.64,.99)):
            m=re.fullmatch(r"(\d{1,4})[/／](\d{1,3})",normalize(t.text))
            if m:values.append(int(m[1]))
    return values[0] if len(values)==1 else None
