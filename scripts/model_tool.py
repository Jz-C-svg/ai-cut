"""
AI Cut 模型调用工具 - 直接命令行调用
用法：
  python model_tool.py vision 图片路径 "你想问什么"
  python model_tool.py text "你想说什么"
  python model_tool.py analyze 视频路径
"""
import json, os, sys, base64, glob, urllib.request
from pathlib import Path
from PIL import Image
API_KEY = __import__('os').environ.get('AICUT_GLM_API_KEY', '')

def glm_vision(img_path, question):
    """视觉理解：看图片回答问题"""
    img = Image.open(img_path)
    img.thumbnail((512, 512))
    tmp = str(img_path) + '.tmp.jpg'
    img.save(tmp, quality=70)
    with open(tmp, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode()
    os.remove(tmp)
    data = json.dumps({'model': 'glm-4v-flash', 'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': question}, {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b64}'}}]}], 'max_tokens': 500, 'temperature': 0.3}).encode()
    req = urllib.request.Request('https://open.bigmodel.cn/api/paas/v4/chat/completions', data=data, headers={'Authorization': f'Bearer {API_KEY}', 'Content-Type': 'application/json'})
    r = urllib.request.urlopen(req, timeout=60)
    resp = json.loads(r.read())
    return resp['choices'][0]['message']['content']

def glm_text(prompt):
    """文本决策：纯文字推理"""
    data = json.dumps({'model': 'glm-4-flash', 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': 2000, 'temperature': 0.5}).encode()
    req = urllib.request.Request('https://open.bigmodel.cn/api/paas/v4/chat/completions', data=data, headers={'Authorization': f'Bearer {API_KEY}', 'Content-Type': 'application/json'})
    r = urllib.request.urlopen(req, timeout=120)
    resp = json.loads(r.read())
    return resp['choices'][0]['message']['content']

def analyze_video(video_path, interval=40):
    """分析视频：抽帧+逐帧视觉理解"""
    work = str(Path(video_path).parent / 'frames')
    Path(work).mkdir(parents=True, exist_ok=True)
    os.system(f'ffmpeg -y -i "{video_path}" -vf "fps=1/{interval}" -q:v 3 "{work}/frame_%04d.jpg" 2>nul')
    frames = sorted(glob.glob(f'{work}/*.jpg'))
    results = []
    for i, frame in enumerate(frames):
        idx = int(Path(frame).stem.split('_')[1])
        t = idx * interval
        print(f'分析帧 {i + 1}/{len(frames)} (t={t}s)...')
        desc = glm_vision(frame, '用中文详细描述这张图：人物、动作、场景、情感氛围。')
        results.append({'time': t, 'desc': desc})
    out = str(Path(video_path).parent / 'analysis.json')
    Path(out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'分析完成: {out}')
    return results
if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == 'vision':
        img = sys.argv[2]
        q = sys.argv[3] if len(sys.argv) > 3 else '描述这张图'
        print(glm_vision(img, q))
    elif cmd == 'text':
        prompt = sys.argv[2]
        print(glm_text(prompt))
    elif cmd == 'analyze':
        video = sys.argv[2]
        interval = int(sys.argv[3]) if len(sys.argv) > 3 else 40
        analyze_video(video, interval)
    else:
        print(f'未知命令: {cmd}')
        print(__doc__)
