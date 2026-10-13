"""Make a cinematic video from the generated master; FFmpeg video transforms only."""
from pathlib import Path
import array, json, math, subprocess, wave
from motion_engine import render_shots
ROOT=Path(__file__).resolve().parent; ASSETS=ROOT/'assets'; BUILD=ROOT/'media-build'; BUILD.mkdir(exist_ok=True)
def run(args):
    p=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',*args],cwd=ROOT,capture_output=True)
    if p.returncode: raise RuntimeError(p.stderr.decode('utf-8','replace')[-2500:])
# Original warm piano-like ambient composition, deterministic additive synthesis.
rate=48000; duration=14
notes=[(0,130.81),(0.1,196),(1.5,261.63),(3,329.63),(4.5,146.83),(5,220),(6.5,293.66),(8,349.23),(9,130.81),(9.2,196),(10.5,261.63),(12,329.63)]
samples=array.array('h')
for i in range(rate*duration):
    t=i/rate; value=0
    for start,f in notes:
        dt=t-start
        if 0<=dt<7:
            env=(1-math.exp(-dt*45))*math.exp(-dt*.62)
            value+=env*(math.sin(2*math.pi*f*dt)+.32*math.sin(2*math.pi*f*2.001*dt)+.1*math.sin(2*math.pi*f*3.004*dt))*.075
    fade=min(1,t/1.2,(duration-t)/2);samples.append(int(max(-.9,min(.9,value*max(0,fade)))*32767))
with wave.open(str(BUILD/'score.wav'),'wb') as out: out.setnchannels(1);out.setsampwidth(2);out.setframerate(rate);out.writeframes(samples.tobytes())
render_shots()
run(['-i',str(BUILD/'shot-0.mp4'),'-i',str(BUILD/'shot-1.mp4'),'-i',str(BUILD/'shot-2.mp4'),'-i',str(BUILD/'score.wav'),'-filter_complex','[0:v][1:v]xfade=transition=fade:duration=0.5:offset=4.5[v1];[v1][2:v]xfade=transition=fade:duration=0.5:offset=9,fade=t=in:st=0:d=0.5,fade=t=out:st=13.4:d=0.6[v];[3:a]loudnorm=I=-20:TP=-2:LRA=7[a]','-map','[v]','-map','[a]','-t','14','-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-movflags','+faststart',str(ASSETS/'source.mp4')])
ass='''[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,Microsoft YaHei,44,&H00E9F2F8,&H00FFFFFF,&H00201710,&H00000000,0,0,0,0,100,100,5,0,1,0.4,0,8,40,40,150,1
Style: Detail,Georgia,17,&H0085BBDD,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,4,0,1,0,0,8,20,20,85,1
Style: Label,Microsoft YaHei,13,&H008F9FAF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,1,0,1,0,0,2,30,30,62,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:14.00,Label,,0,0,0,,AI 生成影像 · 原创动态设计
Dialogue: 0,0:00:00.70,0:00:04.25,Detail,,0,0,0,,{\\fad(650,450)}THE QUIET RITUAL
Dialogue: 0,0:00:01.10,0:00:04.30,Main,,0,0,0,,{\\fad(700,450)}把时间\\N留给香气
Dialogue: 0,0:00:05.20,0:00:08.75,Detail,,0,0,0,,{\\fad(650,450)}BREW WITH INTENTION
Dialogue: 0,0:00:05.65,0:00:08.80,Main,,0,0,0,,{\\fad(700,450)}慢一点\\N更有滋味
Dialogue: 0,0:00:09.60,0:00:13.45,Detail,,0,0,0,,{\\fad(650,650)}A MOMENT TO YOURSELF
Dialogue: 0,0:00:10.05,0:00:13.50,Main,,0,0,0,,{\\fad(700,700)}一杯温暖\\N刚刚好
'''
ass=ass.replace('{\\fad(650,450)}','{\\an8\\move(360,104,360,85,0,650)\\fad(500,450)}').replace('{\\fad(650,650)}','{\\an8\\move(360,104,360,85,0,650)\\fad(500,650)}')
ass=ass.replace('{\\fad(700,450)}','{\\an8\\move(360,182,360,150,0,800)\\fscx96\\fscy96\\t(0,800,1,\\fscx100\\fscy100)\\fad(500,450)}').replace('{\\fad(700,700)}','{\\an8\\move(360,182,360,150,0,800)\\fscx96\\fscy96\\t(0,800,1,\\fscx100\\fscy100)\\fad(500,700)}')
(BUILD/'cinema.ass').write_text(ass,encoding='utf-8')
run(['-i',str(ASSETS/'source.mp4'),'-vf','ass=media-build/cinema.ass','-c:v','libx264','-preset','fast','-crf','18','-c:a','copy','-movflags','+faststart',str(ASSETS/'showcase.mp4')])
run(['-ss','10.8','-i',str(ASSETS/'showcase.mp4'),'-frames:v','1','-q:v','2',str(ASSETS/'poster.jpg')])
plan={'title':'把时间，留给香气','summary':'预置演示方案：咖啡豆微距、手冲过程、杯子收尾；短句字幕与原有暖色光影相配。AI图像合成：平滑推镜、动态蒸汽与水流高光。','segments':[{'start':0,'end':4.5,'subtitle':'把时间，留给香气','observation':'咖啡豆微距','reason':'细节开场建立质感'},{'start':4.5,'end':9,'subtitle':'慢一点，更有滋味','observation':'手冲咖啡与注水画面','reason':'流程镜头推进信息'},{'start':9,'end':14,'subtitle':'一杯温暖，刚刚好','observation':'咖啡杯与蒸汽','reason':'杯子完整收尾'}]}
(ASSETS/'sample-plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
print('Cinema source and animated showcase rendered: 720x1280 / 14s / 30fps')
