"""后端桥接：在 QThread 中以子进程方式调用工程脚本，实时回传日志/进度。

设计要点：
- 始终使用配置中的 python_exe（工程 venv）运行 scripts/*.py，cwd=工程根，
  PYTHONPATH=<root>/lib。源码运行与打包后运行行为一致。
- 不把 torch / faster-whisper / Ollama 模型打进 EXE，重型依赖走 venv。
- 所有输出按 UTF-8 解码，逐行 emit，避免 UI 冻结。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .appconfig import GuiConfig


# ---------------------------------------------------------------------------
# 子进程执行
# ---------------------------------------------------------------------------
def build_env(cfg: GuiConfig) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(cfg.lib_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    env["HF_ENDPOINT"] = env.get("HF_ENDPOINT", "https://hf-mirror.com")
    env["HF_HUB_DISABLE_XET"] = "1"
    return env


class CmdRunner:
    """在当前线程同步跑一个命令，逐行回调 on_line。返回 (returncode, 合并输出)。"""

    def __init__(self, cfg: GuiConfig):
        self.cfg = cfg
        self.aborted = False

    def run(self, argv: list[str], on_line, cwd: str | None = None) -> int:
        cmd = [self.cfg.python_exe(), *argv]
        joined = " ".join(f'"{a}"' if " " in a else a for a in cmd)
        on_line(f"$ {joined}", "cmd")
        sys.path.insert(0, str(self.cfg.lib_dir))
        from aicut.jobs import run_command, Cancelled
        try:
            return run_command(cmd, cwd=cwd or str(self.cfg.project_root),
                               env=build_env(self.cfg), cancelled=lambda: self.aborted,
                               on_line=lambda line: on_line(line, 'stdout'))
        except Cancelled:
            on_line('任务已取消', 'warn')
            return 130
        except subprocess.CalledProcessError as exc:
            on_line(f'进程退出码 {exc.returncode}', 'err')
            return exc.returncode

    def abort(self):
        self.aborted = True



# ---------------------------------------------------------------------------
# 管线编排 Worker
# ---------------------------------------------------------------------------
class PipelineWorker(QThread):
    log = Signal(str, str)          # 文本, 级别
    progress = Signal(int, str)     # 0-100, 阶段说明
    stage = Signal(str)             # 当前阶段中文名
    finished = Signal(bool, str)     # 成功, 结果/路径
    material_ready = Signal(dict)   # preprocess 完成后回传 manifest 项

    def __init__(self, cfg: GuiConfig, mode: str, payload: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.mode = mode            # analyze / edit / quality / open_draft
        self.payload = payload
        self._runner = CmdRunner(cfg)
        self._want_abort = False
        self.task_id = uuid.uuid4().hex

    def abort(self):
        self._want_abort = True
        task = self.cfg.project_root / 'work' / 'tasks' / self.task_id
        if task.is_dir():
            (task / 'cancel').touch()
            # Allow the coordinator to persist cancelled state before hard stop.
            import threading
            threading.Timer(5, self._runner.abort).start()
        else:
            self._runner.abort()

    # -- 日志辅助 --
    def _log(self, msg: str, level: str = "info"):
        self.log.emit(msg, level)

    def _run(self, argv: list[str]) -> int:
        if self._want_abort:
            return -1
        def emit(message, level):
            self.log.emit(message, level)
            if message.startswith('[阶段] '):
                self.stage.emit(message[5:])
        rc = self._runner.run(argv, emit)
        if rc:
            from aicut.jobs import mark_interrupted
            mark_interrupted(self.cfg.project_root, self.task_id,
                             'cancelled' if self._want_abort else 'failed',
                             f'后端进程退出: {rc}')
        return rc

    # ------------------------------------------------------------------ run
    def run(self):
        try:
            if self.mode == "analyze":
                self._do_analyze()
            elif self.mode == "edit":
                self._do_edit()
            elif self.mode == 'build_approved':
                self._do_build_approved()
            elif self.mode == "quality":
                self._do_quality()
            elif self.mode == "tts_preview":
                self._do_tts_preview()
            elif self.mode == "template_analyze":
                self._do_template_analyze()
            elif self.mode == "template_benchmark":
                self._do_template_benchmark()
            elif self.mode == "template_edit":
                self._do_template_edit()
            else:
                self.finished.emit(False, f"未知任务: {self.mode}")
        except Exception as exc:  # noqa: BLE001
            self._log(f"任务异常: {exc}", "err")
            self.finished.emit(False, str(exc))

    # ---------------- 分析链路 ----------------
    def _manifest_item(self, source_path: str) -> dict | None:
        mp = self.cfg.project_root / "work" / "manifest.json"
        if not mp.exists():
            return None
        data = json.loads(mp.read_text(encoding="utf-8"))
        src = str(Path(source_path))
        for it in data.get("items", []):
            if os.path.normcase(it.get("source", "")) == os.path.normcase(src):
                return it
        return None

    def _do_analyze(self):
        self.stage.emit('素材分析')
        self.progress.emit(10, '正在预处理、转写和分析…')
        rc = self._run([str(self.cfg.scripts_dir / 'pipeline.py'), '--video',
                        self.payload['source'], '--analyze-only', '--task-id', self.task_id])
        report_path = self.cfg.project_root / 'work' / 'tasks' / self.task_id / 'status.json'
        report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
        item = report.get('artifacts', {}).get('material')
        if item:
            self.material_ready.emit(item)
        if rc == 0 and item:
            self.progress.emit(100, '分析完成')
            self.finished.emit(True, item['video'])
        else:
            self.finished.emit(False, f'分析未完成，请查看任务报告：{report_path}')

    # ---------------- 出片 ----------------
    def _do_edit(self):
        video = self.payload["video"]
        root = self.cfg.project_root
        argv = [str(self.cfg.scripts_dir / "pipeline.py"), "--video", video, "--task-id", self.task_id]
        if not self.payload.get('knowledge', self.cfg.get('knowledge', True)):
            argv.append('--no-knowledge')
        if self.payload.get('smart'):
            argv.append('--smart')
        if self.payload.get('goal'):
            argv += ['--goal', self.payload['goal']]

        if self.payload.get("auto_style", True):
            argv += ["--auto-style"]
        elif self.payload.get("style_id"):
            argv += ["--style", self.payload["style_id"]]

        if self.payload.get("semantic", True):
            argv += ["--semantic"]
        else:
            argv += ['--no-semantic']

        kf = self.payload.get("keyframes")
        if kf and kf != "none":
            argv += ["--keyframes", kf]

        voiceover = (self.payload.get("voiceover") or "").strip()
        if voiceover:
            argv += ["--voiceover", voiceover, "--voice", self.payload.get("voice") or "zh-CN-XiaoxiaoNeural"]

        if self.payload.get("cover"):
            argv += ["--cover", self.payload["cover"]]
        if self.payload.get("cta"):
            argv += ["--cta", self.payload["cta"]]

        # v3.7.0 智能画面适配：默认开启，可被面板关闭；密度 low/medium/high
        if self.payload.get("visual_adapter", True):
            density = self.payload.get("visual_density") or "medium"
            if density not in ("low", "medium", "high"):
                density = "medium"
            argv += ["--visual-adapter", "--visual-density", density]
        else:
            argv += ["--no-visual-adapter"]

        if self.payload.get("publish", True):
            argv += ["--publish"]

        # v5.2.0 Copilot 方法论：设置页开启后，成片自动进入 Agent 复审（偏好学习+断点队列）
        if self.cfg.get("copilot", False):
            argv += ["--copilot"]

        self.stage.emit("一键成片")
        self.progress.emit(10, "正在决策风格与剪辑…")
        rc = self._run(argv)
        if rc == 0:
            report_path = root / 'work' / 'tasks' / self.task_id / 'status.json'
            report = json.loads(report_path.read_text(encoding='utf-8'))
            if report.get('state') == 'planned':
                self.progress.emit(100, '剪辑方案已生成，等待确认')
                self.finished.emit(True, 'PLAN:' + str(report_path))
            elif report.get('state') == 'completed':
                self.progress.emit(100, '草稿任务完成')
                self.finished.emit(True, str(report.get('artifacts', {}).get('draft') or root / '06-drafts'))
            else:
                self.finished.emit(False, f'任务状态未完成：{report_path}')
        else:
            self.finished.emit(False, "任务未完成或需要人工复审，请查看日志和 work/tasks 任务报告")

    def _do_build_approved(self):
        software = self.payload.get('software', 'jianying')
        engine = self.payload.get('engine', 'project')
        self.stage.emit('生成可恢复草稿' if engine == 'project' else '渲染并校验成片')
        report = self.cfg.project_root / 'work' / 'deliveries' / self.task_id / 'delivery.json'
        argv = [str(self.cfg.scripts_dir / 'deliver_edit.py'), '--sheet', self.payload['sheet'],
                '--software', software, '--engine', engine, '--report-out', str(report),
                '--approve-plan-sha256', self.payload['sha256']]
        if self.payload.get('publish') and software == 'jianying' and engine == 'project':
            argv.append('--publish')
        rc = self._run(argv)
        self.finished.emit(rc == 0, 'DELIVERY:' + str(report) if rc == 0 else '交付未完成，请查看日志和 ' + str(report))

    # ---------------- 质检 ----------------
    def _do_quality(self):
        target = self.payload["target"]
        self.stage.emit("画面质检")
        self.progress.emit(30, "正在质检…")
        rc = self._run([str(self.cfg.scripts_dir / "check_quality.py"), target, "--json"])
        if rc == 0:
            self.progress.emit(100, "质检完成")
            self.finished.emit(True, str(self.cfg.project_root / "reports"))
        else:
            self.finished.emit(False, "质检未通过或失败")

    def _do_tts_preview(self):
        # Edge-TTS 试听：直接调 tts_voice.py 若存在，否则用 edge_tts CLI 思路
        text = self.payload.get("voiceover", "").strip()
        voice = self.payload.get("voice", "zh-CN-XiaoxiaoNeural")
        if not text:
            self.finished.emit(False, "请先输入配音文案")
            return
        self.stage.emit("配音试听")
        out = str(self.cfg.thumbnail_dir() / "tts_preview.mp3")
        code = (
            "import sys, asyncio; sys.path.insert(0, r'%s');"
            "import edge_tts;"
            "async def go():\n"
            "    c = edge_tts.Communicate(%r, %r); await c.save(%r)\n"
            "asyncio.run(go())"
            % (str(self.cfg.lib_dir), text, voice, out)
        )
        rc = self._run(["-c", code])
        if rc == 0 and Path(out).exists():
            self.finished.emit(True, out)
        else:
            self.finished.emit(False, "配音试听失败（需联网 Edge-TTS）")

    # ---------------- 模板化剪辑（v3.8.0） ----------------
    def _do_template_analyze(self):
        """template_analyze：对素材生成视频剪辑指纹，结果写到 out_json。"""
        video = self.payload["video"]
        out_json = self.payload["out_json"]
        self.stage.emit("分析素材指纹")
        self.progress.emit(15, "正在生成视频剪辑指纹…")
        code = (
            "import sys, json; sys.path.insert(0, {lib!r});"
            "from aicut import videofinger;"
            "fp = videofinger.build({video!r});"
            "open({out!r}, 'w', encoding='utf-8').write(json.dumps(fp, ensure_ascii=False));"
            "r = fp.get('rhythm') or {}; s = fp.get('structure') or {};"
            "print('[指纹] 类型=%s 时长=%.1fs 均镜=%.2fs 节奏=%s 钩子=%s' "
            "% (fp.get('video_type','?'), fp.get('duration') or 0,"
            " r.get('avg_shot') or 0, r.get('pace') or '?', s.get('hook_type','?')))"
        ).format(lib=str(self.cfg.lib_dir), video=video, out=out_json)
        rc = self._run(["-c", code])
        if rc == 0 and Path(out_json).exists():
            self.progress.emit(100, "指纹生成完成")
            self.finished.emit(True, out_json)
        else:
            self.finished.emit(
                False, "指纹生成失败（素材可能缺转写/镜头档案，可先跑一次「开始分析」）")

    def _do_template_benchmark(self):
        """template_benchmark：跑 find_benchmark.py，结果写到 out_json。"""
        video = self.payload["video"]
        out_json = self.payload["out_json"]
        maxn = int(self.payload.get("max", 3))
        self.stage.emit("联网找参考")
        self.progress.emit(10, "正在搜索/下载参考并比对打分…")
        argv = [str(self.cfg.scripts_dir / "find_benchmark.py"),
                "--video", video, "--max", str(maxn), "--output", out_json]
        rc = self._run(argv)
        if rc == 0 and Path(out_json).exists():
            self.progress.emit(100, "参考比对完成")
            self.finished.emit(True, out_json)
        else:
            # find_benchmark 自身有降级（本地模拟参考），失败时仍可能有结果
            if Path(out_json).exists():
                self.finished.emit(True, out_json)
            else:
                self.finished.emit(False, "找参考失败，请查看日志（网络/yt-dlp 不可用）")

    def _do_template_edit(self):
        """template_edit：调 auto_edit.py --template 套用模板出片。"""
        video = self.payload["video"]
        template = self.payload["template"]
        name = self.payload.get("name") or f"{Path(video).stem}-templated"
        argv = [str(self.cfg.scripts_dir / "pipeline.py"),
                "--video", video, "--template", str(template),
                "--name", str(name), "--semantic", "--task-id", self.task_id]
        if self.payload.get("publish", True):
            argv.append("--publish")
        self.stage.emit("套用模板出片")
        self.progress.emit(10, "正在按模板剪辑…")
        rc = self._run(argv)
        if rc == 0:
            self.progress.emit(100, "模板化成片草稿已生成")
            self.finished.emit(True, str(self.cfg.project_root / "06-drafts"))
        else:
            self.finished.emit(False, "模板化出片失败，请查看日志")


# ---------------------------------------------------------------------------
# 状态检测 Worker
# ---------------------------------------------------------------------------
class StatusCheckWorker(QThread):
    done = Signal(dict)

    def __init__(self, cfg: GuiConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg

    def run(self):
        res = {
            "ollama": self._check_ollama(),
            "whisper": self._check_whisper(),
            "jianying": self._check_jianying(),
            "ffmpeg": self._check_ffmpeg(),
            "edge_tts": self._check_network(),
            "python": self._check_python(),
        }
        self.done.emit(res)

    def _check_ollama(self):
        host = self.cfg.get("ollama_host", "http://127.0.0.1:11434")
        try:
            with urllib.request.urlopen(host + "/api/tags", timeout=2) as r:
                data = json.loads(r.read().decode("utf-8"))
                models = [m.get("name", "") for m in data.get("models", [])]
            return {"ok": True, "detail": f"在线，模型 {len(models)} 个"}
        except Exception:
            return {"ok": False, "detail": "未检测到 Ollama（127.0.0.1:11434）"}

    def _check_whisper(self):
        d = Path(self.cfg.get("models_dir", ""))
        hit = list(d.glob("*faster-whisper*")) if d.exists() else []
        if hit:
            return {"ok": True, "detail": f"模型目录就绪：{d.name}"}
        return {"ok": False, "detail": f"未在 {d} 找到 faster-whisper 模型"}

    def _check_jianying(self):
        p = Path(self.cfg.get("jianying_exe", ""))
        if p.exists():
            return {"ok": True, "detail": p.name}
        return {"ok": False, "detail": "未找到剪映专业版（可在设置配置路径）"}

    def _check_ffmpeg(self):
        try:
            out = subprocess.run(
                ["ffmpeg", "-version"], capture_output=True, text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=5,
            )
            v = (out.stdout or out.stderr).splitlines()[0] if (out.stdout or out.stderr) else ""
            return {"ok": out.returncode == 0, "detail": v[:60] or "可用"}
        except Exception:
            return {"ok": False, "detail": "ffmpeg 不在系统 PATH"}

    def _check_network(self):
        try:
            urllib.request.urlopen("https://speech.platform.bing.com", timeout=3)
            return {"ok": True, "detail": "可连接 Edge-TTS 服务"}
        except Exception:
            # 404/超时也算“有网络”，仅探测连通性
            try:
                urllib.request.urlopen("https://www.baidu.com", timeout=3)
                return {"ok": True, "detail": "网络正常（Edge-TTS 需联网）"}
            except Exception:
                return {"ok": False, "detail": "无网络，配音功能不可用"}

    def _check_python(self):
        p = Path(self.cfg.python_exe())
        if p.exists():
            return {"ok": True, "detail": str(p)}
        return {"ok": False, "detail": f"未找到解释器: {p}"}
