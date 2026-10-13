r"""剪辑主体：闭环跑完一个目标。

循环：
  感知 读草稿 + 素材画面 + 停顿 + 识别结果 + 你的偏好
  规划 目标 -> 步骤（技能调用）
  执行 每步先快照，再动手（工程通道为主）
  验证 草稿 diff + 断言；不通过就回滚重试，再不行就如实报告
  记账 每一步的感知-决策-动作-结果写进 memory.sqlite

和脚本的区别只有一条：**它会检查自己做的事，并且记住结果。**
"""
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from . import guard
from . import shotprof
from . import learn
from . import hints
from . import plan as plan_mod
from . import architecture as arch_mod
from . import skills
from . import snapshot as snap_mod
from . import verify
from . import vision
from .draft import Draft
from .memory import open_memory

# 某步"什么都没干"时，放宽参数再试一次（演示型自适应）
def _adapt_table() -> dict:
    """Relaxed params for no-op steps, from perception config."""
    from . import perception
    return dict(perception.get("adapt") or {})


@dataclass
class StepResult:
    skill: str
    args: dict
    why: str = ""
    ok: bool = False
    attempts: int = 0
    snapshot: str = ""
    duration_before: float = 0.0
    duration_after: float = 0.0
    checks: list[str] = field(default_factory=list)
    report: dict = field(default_factory=dict)
    note: str = ""
    error: str = ""

    def line(self) -> str:
        mark = "OK  " if self.ok else "FAIL"
        head = f"[{mark}] {self.skill} {json.dumps(self.args, ensure_ascii=False)}"
        tail = f" {self.duration_before:.2f}s -> {self.duration_after:.2f}s"
        if self.note:
            tail += f" · {self.note}"
        if self.attempts > 1:
            tail += f"（试了 {self.attempts} 次）"
        if self.error:
            tail += f" · {self.error}"
        return head + tail


def summarize_report(report: dict) -> str:
    """把技能的返回压成一句人话——尤其是"什么都没做"也要说出来。"""
    if not report:
        return ""
    if report.get("error"):
        return "错误：" + str(report["error"])[:120]
    if report.get("skipped"):
        return "跳过：" + str(report["skipped"])[:120]
    if report.get("ok") is False or report.get("rc") not in (None, 0):
        tail = ((report.get("stderr_tail") or "").strip().splitlines()
                or (report.get("stdout_tail") or "").strip().splitlines())
        return f"出片失败（rc={report.get('rc')}）：{' / '.join(x[:120] for x in tail[-3:]) if tail else '无 stderr'}"
    parts: list[str] = [str(report["note"])] if report.get("note") else []
    removed = report.get("removed")
    if isinstance(removed, (int, float)) and removed:
        parts.append(f"剪掉 {removed:.2f}s")
    if report.get("spans") and not report.get("spans"):
        parts.append("没有找到符合条件的停顿")
    if report.get("applied") is not None and not report.get("applied"):
        parts.append("没有命中可删内容")
    if report.get("marked") and not report.get("applied"):
        parts.append("识别标了要删的句子但没匹配上逐字稿")
    if report.get("split"):
        parts.append(f"切分 {len(report['split'])} 处")
    if report.get("deleted"):
        parts.append(f"整段删除 {len(report['deleted'])} 段")
    if report.get("result_draft"):
        parts.append("新草稿 " + Path(report["result_draft"]).name)
    return "；".join(parts)


def _is_noop(report: dict) -> bool:
    if not report or report.get("error") or report.get("ok") is False:
        return False
    for key in ("removed", "applied", "spans"):
        value = report.get(key)
        if value in (0, 0.0, []) :
            return True
    return False


class Agent:
    def __init__(self, *, draft: str | Path | None = None, video: str | Path | None = None,
                 goal: str = "", dry_run: bool = False, provider: str = "auto",
                 max_steps: int = 8, retries: int = 1, task: str | None = None,
                 use_rules: bool = False, use_vision: bool = True, log=print,
                 max_iterations: int = 3, render_threshold: float | None = None,
                 copilot_method: bool = False,
                 platform: str = "douyin", production: dict | None = None) -> None:
        self.target = Path(draft) if draft else None
        self.goal = goal
        self.dry_run = dry_run
        self.provider = provider
        self.max_steps = max_steps
        self.retries = retries
        self.use_rules = use_rules
        self.use_vision = use_vision
        self.log = log
        # TOP1 Editor-Critic 迭代循环
        self.max_iterations = max_iterations
        self.render_threshold = render_threshold  # None 用 review_film 默认值
        self.mem = open_memory()
        self.draft = Draft.load(self.target) if self.target else None
        self.video = Path(video) if video else self._infer_video()
        name = self.draft.name if self.draft else "material"
        self.task = task or f"{time.strftime('%m%d-%H%M%S')}-{name}"
        self.steps: list[StepResult] = []
        self.created: list[str] = []
        self.state: dict = {}
        self.production = dict(production or {})
        # Copilot 方法论开关（默认关，不影响现有行为与回归；环境变量 AICUT_COPILOT_METHOD=1 可开）
        self.copilot_method = bool(copilot_method)
        if not self.copilot_method:
            try:
                import os as _os
                self.copilot_method = _os.environ.get("AICUT_COPILOT_METHOD") == "1"
            except Exception:  # noqa: BLE001
                self.copilot_method = False
        self.platform = (platform or "douyin")  # 目标平台（默认抖音），传给感知/规划/意图
        self._export_report = None  # ExportReport 实例（run() 中创建）

    # ---------- Copilot 方法论钩子（Codex 6.0 工作法，全部可选、全 try/except） ----------

    def _cop(self):
        """惰性取 copilot_method 模块；未开或导入失败返回 None，绝不影响主流程。"""
        if not self.copilot_method:
            return None
        try:
            from . import copilot_method as _cm
            return _cm
        except Exception:  # noqa: BLE001
            return None

    def _cop_plan_gate(self, plan: dict) -> None:
        """Play Mode 规划门：感知→规划之间。校验步骤清单是否含 动作/预期/验证方式。"""
        cm = self._cop()
        if cm is None:
            return
        try:
            ok, issues = cm.validate_plan(plan)
            if issues:
                self.log("  [Copilot·PlayMode] 规划门提示：" + "；".join(issues[:5]))
            self.mem.add_episode(task=self.task, kind="copilot_plan_gate", goal=self.goal,
                                 detail={"ok": ok, "issues": issues}, ok=ok)
        except Exception:  # noqa: BLE001
            pass

    def _cop_step_note(self, res: StepResult) -> None:
        """Terminal 长任务循环：记录失败/重试到会话笔记（先读错误再改方案的依据）。"""
        cm = self._cop()
        if cm is None or res.ok:
            return
        try:
            cm.merge_session_note(cm.SessionNote(
                task=self.task, goal=self.goal,
                failure_retries=[{"step": res.skill, "args": res.args,
                                  "attempts": res.attempts, "error": res.error[:200],
                                  "note": res.note[:200]}]))
        except Exception:  # noqa: BLE001
            pass

    def _cop_final_review(self, plan: dict, result: dict) -> None:
        """Review Mode 收尾：计划 vs 已执行差异审查（遗漏/偏差/风险）。"""
        cm = self._cop()
        if cm is None:
            return
        try:
            rv = cm.review_plan(plan, self.steps)
            result["copilot_review"] = rv
            self.log("  [Copilot·ReviewMode] " + rv["summary"])
            for it in rv["issues"][:5]:
                self.log(f"    · {it['type']}/{it['severity']}: {it['detail'][:80]}")
            cm.merge_session_note(cm.SessionNote(
                task=self.task, goal=self.goal,
                global_decisions=[f"收尾审查：{rv['summary']}"]))
        except Exception:  # noqa: BLE001
            pass

    # ---------- Codex 6 第二轮钩子（copilot_v2：Subagents / 偏好学习 / 计划队列） ----------

    def _cop_v2(self):
        """惰性取 copilot_v2 模块；未开或导入失败返回 None，绝不影响主流程。"""
        if not self.copilot_method:
            return None
        try:
            from . import copilot_v2 as _v2
            return _v2
        except Exception:  # noqa: BLE001
            return None

    def _cop_v2_plan(self, plan: dict) -> None:
        """规划后：委派预演（三角色编排）+ 计划队列持久化（断点续跑）。"""
        v2 = self._cop_v2()
        if v2 is None:
            return
        try:
            state_s = (f"素材={len(getattr(self.draft, 'video_segments', None) or [])} 段，"
                       f"目标={self.goal[:60]}")
            d = v2.delegate("plan", self.goal, state=state_s)
            self.log(f"  [Subagents] 委派 {d['role']}：{self.goal[:60]}")
            self.mem.add_episode(task=self.task, kind="subagent_delegate",
                                 goal=self.goal, detail={"role": d["role"]}, ok=True)
            steps = plan.get("步骤") or []
            if steps:
                v2.save_queue(v2.PlanQueue(task=self.task, goal=self.goal, steps=steps))
                self.log(f"  [PlanQueue] 已持久化 {len(steps)} 步计划，中断可从断点续跑")
        except Exception:  # noqa: BLE001
            pass

    def _cop_v2_mark(self, step: dict, res, step_index: int = 0) -> None:
        """每步完成后更新队列进度。step_index 为主循环 1-based 序号（plan 步骤可能无序号键）。"""
        v2 = self._cop_v2()
        if v2 is None:
            return
        try:
            idx = int(step_index) or int(step.get("序号", step.get("index", 0))) or 0
            if idx:
                v2.mark_done(self.task, idx, {"ok": bool(getattr(res, "ok", False))})
        except Exception:  # noqa: BLE001
            pass

    def _material_type(self) -> str:
        """从目标关键词推断素材类型（口播/母婴/…），未知返回『通用』。"""
        g = (self.goal or "").lower()
        for kw, typ in [("母婴", "母婴"), ("baby", "母婴"), ("口播", "口播"),
                        ("vlog", "Vlog"), ("风景", "风景"), ("旅行", "旅行")]:
            if kw in g:
                return typ
        return "通用"

    def _cop_v2_learn(self, rv: dict) -> None:
        """Critic 评审后：把 reviewer 修正建议转成偏好学习信号（同类素材复用）。"""
        v2 = self._cop_v2()
        if v2 is None:
            return
        try:
            learned = 0
            for it in (rv.get("issues") or [])[:5]:
                patch = it.get("plan_patch") or {}
                skill = patch.get("skill", "")
                if not skill:
                    continue  # 无明确修正技能的 issue 不入学习库（避免噪音）
                after = {k: v for k, v in (patch.get("args") or {}).items()}
                c = v2.Correction(material_type=self._material_type(), skill=skill,
                                  before={}, after=after,
                                  reason=str(it.get("detail", ""))[:120], source="reviewer")
                v2.capture_correction(self.mem, c, confidence=0.5)
                learned += 1
            if learned:
                self.log(f"  [偏好学习] Critic 修正已入库 {learned} 条（同类素材下次自动参考）")
                self.mem.add_episode(task=self.task, kind="preference_learn",
                                     goal=self.goal, detail={"learned": learned}, ok=True)
        except Exception:  # noqa: BLE001
            pass

    def _cop_v2_inject_prefs(self, state: dict) -> None:
        """学习闭环注入：把同类素材学到的修正规则并入规划输入 preferences。

        让『上次 Critic/用户修正 → memory』在『本次同类素材规划』时直接生效。
        """
        v2 = self._cop_v2()
        if v2 is None:
            return
        try:
            if not self.mem:
                return
            adj = v2.learned_adjustments(self.mem, self._material_type())
            if not adj:
                return
            prefs = list(state.get("preferences") or [])
            for a in adj[:6]:
                rule = a.get("rule", "")
                if rule and rule not in prefs:
                    prefs.append(rule)
            if prefs != state.get("preferences"):
                state["preferences"] = prefs
                self.log(f"  [学习闭环] 已注入 {len(adj)} 条历史修正规则到本次规划（素材类型：{self._material_type()}）")
        except Exception:  # noqa: BLE001
            pass

    def _infer_video(self) -> Path | None:
        if not self.draft or not self.draft.video_segments:
            return None
        p = Path(self.draft.video_segments[0].material_path())
        return p if p.exists() else None

    # ---------- 感知 ----------

    def perceive(self) -> dict:
        from .paths import TRANSCRIPTS, WORK

        state: dict = {"task": self.task, "video": str(self.video) if self.video else None,
                       "platform": getattr(self, "platform", "douyin")}
        if self.production:
            state['production'] = {k: v for k, v in self.production.items() if k != 'approvals'}
            state['production']['user_approved'] = bool(
                (self.production.get('approvals') or {}).get(self.production.get('request_id')))
        if self.draft:
            state["draft"] = {
                "name": self.draft.name,
                "duration": self.draft.declared_duration,
                "tracks": {t.type: len(t.segments) for t in self.draft.tracks},
                "segments": [line.strip() for line in self.draft.describe()][1:],
            }
        name = self.video.stem if self.video else (self.draft.name if self.draft else "")
        if self.video and self.video.exists():
            try:
                state["silences"] = skills.detect_silences(self.video, -30.0, 0.5)
            except Exception as exc:  # noqa: BLE001
                state["silence_error"] = str(exc)[:120]
            if self.use_vision:
                try:
                    state["vision_summary"] = vision.analyze(self.video)["summary"]
                except Exception as exc:  # noqa: BLE001
                    state["vision_error"] = str(exc)[:120]
        state["has_transcript"] = (TRANSCRIPTS / f"{name}.json").exists()
        state["has_understanding"] = (WORK / f"understand-{name}.json").exists()
        state["preferences"] = [p["rule"] for p in self.mem.preferences(limit=12)]
        state["knowledge"] = learn.knowledge_brief(12, self.goal)
        # 案例库 few-shot：按目标语义召回相似历史案例，注入规划上下文
        try:
            from . import case_library as case_lib  # noqa: PLC0415
            state["similar_cases"] = case_lib.brief_for_planner(self.goal, top_k=3)
        except Exception as exc:  # noqa: BLE001
            state["similar_cases_error"] = str(exc)[:120]
        if self.video and self.video.exists():
            try:
                prof = shotprof.build(self.video, max_shots=12, use_cloud=self.use_vision,
                                      progress=lambda *_: None)
                state["shots"] = prof.get("summary") or {}
                self.shot_profile = prof
            except Exception as exc:  # noqa: BLE001
                state["shots_error"] = str(exc)[:160]
        state["recipes"] = learn.recipes()
        state["styles"] = plan_mod.available_styles()
        # 内容架构匹配：有逐字稿时自动匹配最适合的叙事结构
        state["architectures"] = arch_mod.available_architectures()
        if state.get("has_transcript"):
            try:
                segs = skills.load_transcript(name)
                if segs:
                    arch_match = arch_mod.match_architecture(segs, semantic_doc=None, meta={"task": self.task})
                    state["matched_architecture"] = arch_match
                    state["matched_architecture_id"] = (arch_match.get("matched") or {}).get("id", "")
                    # 知识内化：把知识包裁剪文本 + RAG 召回挂到 state，供 plan/intent 阶段使用
                    try:
                        from . import knowledge_pack, arch_rag as _rag
                        _layer = arch_match.get("content_layer", "shortform")
                        _aid = (arch_match.get("matched") or {}).get("id", "")
                        state["knowledge_pack_brief"] = knowledge_pack.get_knowledge_prompt(_layer, _aid)
                        _q = " ".join(str(s.get("text", "")) for s in segs)[:400]
                        state["rag_context"] = _rag.get_rag_context(_q, content_layer=_layer, top_k=3)
                    except Exception as _ke:  # noqa: BLE001
                        state["knowledge_error"] = str(_ke)[:160]
            except Exception as exc:  # noqa: BLE001
                state["architecture_error"] = str(exc)[:160]
        self.state = state
        return state

    # ---------- 执行 ----------

    def _new_ctx(self, before: Draft | None) -> skills.Context:
        from .paths import WORK

        name = self.video.stem if self.video else (self.draft.name if self.draft else "")
        return skills.Context(
            draft=self.draft, before=before, video=self.video,
            transcript=skills.load_transcript(name),
            vision=(self.state or {}).get("vision_summary"),
            memory=self.mem, dry_run=self.dry_run, log=self.log,
            production_approvals=dict(self.production.get('approvals') or {}),
            production_options={k: self.production[k] for k in ('software', 'engine', 'output', 'publish')
                                if k in self.production},
            goal=self.goal,
        )

    def _execute_step(self, step: dict) -> StepResult:
        sid = step.get("skill", "")
        args = dict(step.get("args") or {})
        res = StepResult(skill=sid, args=args, why=step.get("why", ""))
        skill = skills.get(sid)
        if skill is None:
            res.error = "未知技能"
            return res
        problem = guard.precheck(skill, self)
        if problem:
            res.error = problem
            return res
        required = getattr(skill, "required", None) or []
        missing = [k for k in required if k not in args]
        if missing:
            res.error = "参数不全：" + "、".join(missing)
            return res

        while True:
            res.attempts += 1
            before = self.draft.clone() if self.dry_run and self.draft else None
            if not self.dry_run:
                before = Draft.load(self.target) if self.target and self.target.exists() else None
            res.duration_before = before.declared_duration if before else 0.0

            snap = None
            if not self.dry_run and self.target and skill.channel != 'production':
                snap = snap_mod.snapshot(self.target, note=f"{sid} 前")
                res.snapshot = snap["id"]

            ctx = self._new_ctx(before)
            # A5 Stop Gate: 执行前检查草稿合法性，不合法则停止并记录
            if self.draft is not None:
                gate = verify.stop_gate_check(self.draft)
                if not gate["passed"]:
                    res.report = {"error": "Stop Gate: 草稿不合法",
                                  "violations": gate["violations"]}
                    res.error = "Stop Gate 不通过: " + "; ".join(gate["violations"][:3])
                    self.log("  [Stop Gate] " + res.error)
                    self.mem.add_episode(task=self.task, kind="stop_gate", goal=sid,
                                         detail=gate, ok=False)
                    return res
            if self.dry_run and skill.channel in {"shell", "production"}:
                report = {"op": sid, "ok": True, "dry_run": True,
                          "note": hints.dry_note(sid, args)}
            else:
                if self._export_report:
                    self._export_report.log_operation(
                        f"执行步骤#{len(self.steps)+1}", sid,
                        "开始", json.dumps(args, ensure_ascii=False)[:120])
                try:
                    report = skill.run(ctx, args)
                except Exception as exc:  # noqa: BLE001
                    report = {"error": f"{type(exc).__name__}: {exc}"[:300]}
            res.report = report
            if self._export_report:
                _r_ok = not (report.get("error") or report.get("ok") is False)
                self._export_report.log_operation(
                    f"步骤#{len(self.steps)+1}完成", sid,
                    "成功" if _r_ok else "失败",
                    summarize_report(report)[:120])

            produced = report.get("result_draft")
            if produced and Path(produced).exists():
                self.target = Path(produced)
                self.draft = Draft.load(self.target)
                if self.target.name not in self.created:
                    self.created.append(self.target.name)
            elif ctx.draft is not None and skill.channel != 'production':
                self.draft = ctx.draft
                if not self.dry_run and self.target:
                    ctx.draft.save()
                    self.draft = Draft.load(self.target)
            after = self.draft
            res.duration_after = after.declared_duration if after else 0.0
            res.note = summarize_report(report)

            spec = report.get("verify") or skill.verify
            if spec and after is not None:
                vrep = verify.from_spec2(spec, before, after)
                res.checks = vrep.lines()
                res.ok = vrep.ok
            else:
                res.checks = []
                res.ok = True
            sem_q = report.get("semantic") or hints.question_for(sid)
            if sem_q and self.use_vision and not self.dry_run and after is not None and self.video:
                sc = verify.check_semantic(self.video, 0.3, sem_q)
                res.checks.append(sc.line())
                res.ok = res.ok and sc.ok
            if report.get("error") or report.get("ok") is False:
                res.ok = False
                res.error = res.error or summarize_report(report)

            if res.ok:
                # 什么都没干时，放宽阈值再试一次
                adjust = _adapt_table().get(sid)
                if adjust and _is_noop(report) and res.attempts <= 1:
                    self.log(f"  {sid} 没命中任何内容，放宽参数再试：{adjust}")
                    args = {**args, **adjust}
                    res.args = args
                    continue
                break
            if self.dry_run or snap is None or res.attempts > self.retries:
                break
            self.log(f"  第 {res.attempts} 次没通过，回滚后重试")
            snap_mod.restore(snap["id"], backup=False)
            self.draft = Draft.load(self.target) if self.target else None

        if skill is not None and not self.dry_run:
            self.mem.upsert_skill({"id": sid, "goal": skill.goal, "channel": skill.channel,
                                   "verify": skill.verify, "source": "builtin"})
            self.mem.record_run(sid, res.ok)

        detail = {"args": args, "why": res.why, "note": res.note, "checks": res.checks,
                  "duration": [res.duration_before, res.duration_after],
                  "report": {k: v for k, v in res.report.items() if k not in ("stdout_tail", "stderr_tail")}}
        self.mem.add_episode(task=self.task, kind="step", step=len(self.steps) + 1, goal=sid,
                             detail=detail, evidence=res.snapshot, ok=res.ok)
        return res

    # ---------- 主循环（TOP1：Editor-Critic while 迭代，接 critique_loop） ----------

    def _issues_to_steps(self, issues: list[dict]) -> list[dict]:
        """把 Critic 的 issues 翻译成下一轮可执行 steps（Editor 带反馈再迭代）。

        新 schema（critique_loop）：issue["plan_patch"] = {"skill":..., "args":...}
        旧 schema（review_film）：issue["suggested_action"] / issue["suggested_args"]
        两种都兼容。skill 不在 REGISTRY、必填参数补不齐、DONE/RENDER 信号，都跳过。
        """
        out: list[dict] = []
        seen: set = set()
        for it in issues or []:
            patch = it.get("plan_patch") or {}
            sid = patch.get("skill") or it.get("suggested_action") or ""
            if sid in ("", "manual", "DONE", "RENDER"):
                continue
            skill = skills.get(sid)
            if skill is None:
                continue
            args = dict(patch.get("args") or it.get("suggested_args") or {})
            # 用 sample 补默认参数（避免"参数不全"）
            for k, v in (skill.sample or {}).items():
                args.setdefault(k, v)
            # 必填参数缺失则跳过
            missing = [k for k in (skill.required or []) if k not in args]
            if missing:
                continue
            key = (sid, tuple(sorted(args.items())))
            if key in seen:
                continue
            seen.add(key)
            why_tag = it.get("severity") or it.get("type", "")
            out.append({"skill": sid, "args": args,
                        "why": f"Critic[{why_tag}/{it.get('location', '')}]："
                               f"{(it.get('reason') or '')[:16]}"})
        return out

    def _save_state(self, iteration: int, steps: list[dict], rv: dict,
                    stop_reason: str) -> None:
        """durable state：work/agent-state/<task>.json，崩溃可恢复。"""
        try:
            from .paths import WORK
            d = WORK / "agent-state"
            d.mkdir(parents=True, exist_ok=True)
            payload = {
                "task": self.task, "goal": self.goal, "iteration": iteration,
                "max_iterations": self.max_iterations, "stop_reason": stop_reason,
                "next_steps": steps,
                "last_review": {"overall_score": rv.get("overall_score"),
                                 "ready_to_ship": rv.get("ready_to_ship"),
                                 "issues_count": len(rv.get("issues", []))},
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            (d / f"{self.task}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def run(self) -> dict:
        started = time.time()
        # === ExportReport 全流程报告接入 ===
        try:
            from .export_report import ExportReport
            rep = ExportReport(self.draft.name if self.draft else "material")
            rep.dry_run = self.dry_run
            self._export_report = rep
        except Exception:
            rep = None
            self._export_report = None
        if rep:
            rep.stage_start("感知")
        state = self.perceive()
        self.mem.add_episode(task=self.task, kind="perceive", goal=self.goal, detail=state, ok=True)
        if rep:
            rep.stage_end("感知")
            _sp = getattr(self, "shot_profile", {}) or {}
            _summary = _sp.get("summary") or {}
            _shot_count = _summary.get("shots", 0)
            _vlm_calls = len([s for s in (_sp.get("shots") or [])
                              if any(str(src).startswith("vlm:") for src in (s.get("来源") or []))])
            rep.set_analysis_result(shot_count=_shot_count, scene_count=_shot_count, vlm_calls=_vlm_calls)
            _mats = []
            if self.draft:
                seen_paths = set()
                for seg in self.draft.video_segments:
                    mp = seg.material_path()
                    if mp and mp not in seen_paths:
                        seen_paths.add(mp)
                        _mats.append({"path": mp, "name": seg.material_name() or ""})
            rep.set_materials(_mats)
            rep.log_operation("感知完成", self.task, "成功",
                              f"镜头={_shot_count}, VLM调用={_vlm_calls}, 素材={len(_mats)}")
            rep.stage_start("规划")
        # Copilot·v2 学习闭环：把历史学到的修正规则注入规划输入（同类素材下次直接遵守）
        self._cop_v2_inject_prefs(state)
        plan = plan_mod.plan_any(self.goal, state, provider=self.provider, force_rules=self.use_rules)
        self.mem.add_episode(task=self.task, kind="plan", goal=self.goal, detail=plan,
                             ok=bool(plan.get("步骤")))
        # 进化闭环：规划完成后自动留档决策日志（失败案例由 edit_intent 链路沉淀）
        try:
            from . import brain_logger
            _segs = skills.load_transcript(self.video.stem) if self.video else []
            _fp = brain_logger.material_fingerprint(_segs, {"task": self.task})
            _ma = state.get("matched_architecture") or {}
            _matched = (_ma.get("matched") or {}).get("id", "")
            brain_logger.log_decision(
                material_fingerprint=_fp,
                matched_arch=_matched,
                architecture_decision={"matched_arch": _matched,
                                        "决策摘要": plan.get("理解", "")[:200]},
                rule_vs_model_diff={"匹配方法": _ma.get("method", ""),
                                    "候选": [c.get("id") for c in (_ma.get("candidates") or [])[:3]]},
                content_layer=_ma.get("content_layer", "shortform"))
        except Exception:  # noqa: BLE001
            pass
        if rep:
            rep.stage_end("规划")
            rep.set_plan_result(decision=plan.get("理解", ""),
                                style_card=plan.get("来源", ""),
                                steps=len(plan.get("步骤") or []))
            rep.log_operation("规划完成", self.task, "成功",
                              f"理解={plan.get('理解','')[:40]}, 来源={plan.get('来源','')}")
        self.log(f"任务 {self.task}｜理解：{plan.get('理解')}｜规划来源：{plan.get('来源')}")
        # Copilot·PlayMode 规划门：感知→规划之间，校验步骤清单完整性（不阻断）
        self._cop_plan_gate(plan)
        # Copilot·v2 规划后：三角色委派预演 + 计划队列持久化（断点续跑）
        self._cop_v2_plan(plan)
        for warning in plan.get("warnings") or []:
            self.log("  提示：" + str(warning))
        steps = (plan.get("步骤") or [])[: self.max_steps]
        if not steps:
            self.log("没有可执行步骤——目标里没有匹配到任何技能。")
        steps, dropped = guard.filter_steps(steps, state)
        for d in dropped:
            self.log("  跳过：" + d)

        # dry_run：只跑第一轮 plan+演算，不调 review 不迭代（保持现有行为）
        if self.dry_run:
            for i, step in enumerate(steps, 1):
                self.log(f"步骤 {i}/{len(steps)}：{step['skill']}（{step.get('why', '')}）")
                res = self._execute_step(step)
                self.steps.append(res)
                self._cop_step_note(res)
                self._cop_v2_mark(step, res, i)
                self.log("  " + res.line())
                for check in res.checks:
                    self.log("    " + check)
                if not res.ok:
                    self.log("  这一步没通过，停止后续步骤（草稿已回到安全状态）")
                    break
            result = self.report(plan, started)
            self._cop_final_review(plan, result)
            result["review"] = None
            result["iterations"] = []
            result["iteration_count"] = 0
            self.mem.add_episode(task=self.task, kind="result", goal=self.goal,
                                 detail=result, ok=result["ok"])
            # === ExportReport: dry_run 路径 ===
            if rep:
                rep.stage_end("执行")
                _skills_used = list(dict.fromkeys(s.skill for s in self.steps))
                rep.set_execution_result(draft_path=str(self.target) if self.target else None,
                                         skills_called=_skills_used)
                rep.set_review_result(iterations=0, scores_per_round=[],
                                      final_verdict="演算模式（未评审）", ready_to_ship=False)
                rep.set_export_result(export_method="待用户操作", resolution="-", fps=0,
                                      duration=0.0, export_time=0.0, output_path=None)
                rep.set_open_draft_result(method="待用户操作",
                                          timestamp="",
                                          result="双击草稿路径在剪映中打开")
                rep.log_operation("dry_run结束", self.task, "演算完成",
                                  f"步骤={len(self.steps)}, 草稿未实际修改")
                try:
                    rep.save()
                except Exception:
                    pass
            return result

        # ---- Editor-Critic while 迭代循环（TOP1） ----
        if rep:
            rep.stage_start("执行")
        reviews: list[dict] = []
        prev_issues_key: tuple | None = None
        no_progress_rounds = 0
        watchdog: deque = deque(maxlen=3)
        iteration = 0
        final_review: dict | None = None
        stop_reason = "max_iterations"
        for iteration in range(1, self.max_iterations + 1):
            self.log(f"── 迭代轮次 {iteration}/{self.max_iterations} ──")
            # 每轮执行前自动快照（迭代失败可回滚）
            if not self.dry_run and self.target:
                try:
                    snap_mod.snapshot(self.target, note=f"iteration {iteration} 前")
                except Exception:  # noqa: BLE001
                    pass
            # 执行当前 steps（第一轮来自 plan_any，后续轮来自 review issues）
            if not steps:
                self.log("  本轮无步骤可执行")
            for i, step in enumerate(steps, 1):
                self.log(f"  步骤 {i}/{len(steps)}：{step['skill']}（{step.get('why', '')}）")
                res = self._execute_step(step)
                self.steps.append(res)
                self._cop_step_note(res)
                self._cop_v2_mark(step, res, i)
                self.log("    " + res.line())
                # 看门狗：最近 3 次 (skill_id, sorted(args.keys()))，连续 3 次相同 → nudge
                watchdog.append((res.skill, tuple(sorted(res.args.keys()))))
                if len(watchdog) == 3 and len(set(watchdog)) == 1:
                    self.log(f"  ⚠ 看门狗：连续 3 次相同操作 {watchdog[0][0]}，注入 nudge")
                    self.mem.add_episode(task=self.task, kind="nudge", goal=res.skill,
                                         detail={"reason": "连续3次相同操作",
                                                 "watchdog": [list(x) for x in watchdog]},
                                         ok=False)
                for check in res.checks:
                    self.log("      " + check)
                if not res.ok or res.report.get('stop_agent'):
                    stop_reason = res.report.get('status') or 'step_failed'
                    break
            if self.steps and (not self.steps[-1].ok or self.steps[-1].report.get('stop_agent')):
                break
            # 本轮评审（Critic：context isolation，只看当前草稿，不累积历史）
            if not self.target or not self.target.exists():
                self.log("  没有草稿可评审")
                stop_reason = "no_draft"
                break
            try:
                from . import critique_loop
                rv = critique_loop.critique(self.target, video=self.video,
                                             use_vision=self.use_vision,
                                             iteration=iteration, task=self.task)
            except Exception as exc:  # noqa: BLE001
                rv = {"error": str(exc)[:200], "ready_to_ship": False, "issues": [],
                      "iteration": iteration, "overall_score": 0.0}
                self.log(f"  Critic 失败：{rv['error']}")
            reviews.append(rv)
            final_review = rv
            self.log(f"  Critic：{critique_loop.describe(rv)}")
            self.mem.add_episode(task=self.task, kind="review", goal=f"iter{iteration}",
                                 detail=rv, ok=rv.get("ready_to_ship") is True)
            # Copilot·v2：把 Critic 修正建议转为偏好学习信号（同类素材下次复用）
            self._cop_v2_learn(rv)
            # durable state：每轮写 work/agent-state/<task>.json（崩溃可恢复）
            self._save_state(iteration, steps, rv, stop_reason)
            # 终止条件 ①：Critic 通过（overall_score>=8 且 P0=0）
            if rv.get("ready_to_ship") is True:
                self.log("  ✅ ready_to_ship，进入终态")
                stop_reason = "ready_to_ship"
                break
            # 终止条件 ②：连续 1 轮 P0 指纹无变化 → 升级人工（记 warning，不强制退出）
            from . import critique_loop as _cl
            p0_sig = _cl.p0_signature(rv.get("issues", []))
            if prev_issues_key is not None and p0_sig == prev_issues_key and p0_sig:
                no_progress_rounds += 1
                self.log(f"  ⚠ 连续 {no_progress_rounds + 1} 轮 P0 无变化，建议升级人工（继续迭代到上限）")
                self.mem.add_episode(task=self.task, kind="warning", goal=f"iter{iteration}",
                                     detail={"reason": "P0 无变化", "p0_sig": p0_sig}, ok=False)
            else:
                no_progress_rounds = 0
            prev_issues_key = p0_sig
            # issues → 下一轮 steps
            steps = self._issues_to_steps(rv.get("issues", []))
            if not steps:
                self.log("  没有可执行的修复步骤，退出")
                stop_reason = "no_actionable_issue"
                break
        # 终止条件 ③：达 max_iterations（循环自然结束）
        result = self.report(plan, started)
        self._cop_final_review(plan, result)
        result["review"] = final_review
        result["iterations"] = reviews
        result["iteration_count"] = iteration
        result["max_iterations"] = self.max_iterations
        result["stop_reason"] = stop_reason
        self.mem.add_episode(task=self.task, kind="result", goal=self.goal,
                             detail=result, ok=result["ok"])
        # === ExportReport: 正常路径填充+保存 ===
        if rep:
            rep.stage_end("执行")
            rep.stage_start("评审")
            _skills_used = list(dict.fromkeys(s.skill for s in self.steps))
            rep.set_execution_result(draft_path=str(self.target) if self.target else None,
                                     skills_called=_skills_used)
            if final_review:
                rep.set_review_result(
                    iterations=iteration,
                    scores_per_round=[rv.get("overall_score", 0) for rv in reviews],
                    final_verdict="ready_to_ship" if final_review.get("ready_to_ship") else "feedback",
                    ready_to_ship=bool(final_review.get("ready_to_ship")),
                )
            # === 画面质量验证：cleancheck 终检（评审后、导出前） ===
            _clean = None
            try:
                import cv2 as _cv2
                from . import cleancheck
                _vpath = self.video
                if _vpath:
                    _vp = str(_vpath)
                    if os.path.exists(_vp):
                        _cap = _cv2.VideoCapture(_vp)
                        if _cap.isOpened():
                            _cap.set(_cv2.CAP_PROP_POS_MSEC, 1000.0)
                            _ok, _frame = _cap.read()
                            _cap.release()
                            if _ok:
                                _clean = cleancheck.check_frame(_frame)
                rep.set_quality_result(clean=_clean, compare=None)
                if _clean is None:
                    rep.log_operation("画面质量检查", self.task, "skip", "无可用视频帧")
                elif _clean.get("clean_score", 100) < 75:
                    rep.log_operation("画面质量检查", self.task, "warning",
                                      f"干净度 {_clean['clean_score']}/100（{_clean['level']}）"
                                      + "；" + "；".join(_clean.get('issues') or []))
                else:
                    rep.log_operation("画面质量检查", self.task, "pass",
                                      f"干净度 {_clean['clean_score']}/100（{_clean['level']}）")
            except Exception as _exc:  # noqa: BLE001 质量检查失败不阻塞主流程
                rep.log_operation("画面质量检查", self.task, "error", str(_exc)[:120])
            rep.stage_end("评审")
            production = result.get('production')
            if production is not None:
                delivery = production.get('delivery') or {}
                media = delivery.get('media_validation') or {}
                portable = delivery.get('engine') == 'portable'
                rep.set_review_result(iterations=0, scores_per_round=[],
                                      final_verdict=result['status'], ready_to_ship=False)
                rep.set_export_result(
                    export_method=('FFmpeg制作接口' if portable else '草稿制作接口')
                                  if production.get('artifact') else '未执行制作',
                    resolution=f"{media['width']}x{media['height']}" if media else '-', fps=0,
                    duration=media.get('duration', 0.0), export_time=0.0,
                    output_path=production.get('artifact'))
                rep.set_open_draft_result(method='制作接口', timestamp='',
                                          result=('MP4已生成，待人工画面验收' if portable
                                                  else '草稿待目标软件打开验收')
                                                 if production.get('artifact') else result['status'])
            else:
                rep.set_export_result(export_method="待用户操作", resolution="-", fps=0,
                                      duration=0.0, export_time=0.0, output_path=None)
                rep.set_open_draft_result(method="待用户操作", timestamp="",
                                          result="双击草稿路径在剪映中打开后导出")
            rep.log_operation("执行+评审完成", self.task,
                              "ready" if (final_review and final_review.get("ready_to_ship")) else "not_ready",
                              f"迭代={iteration}, 步骤={len(self.steps)}, 终止={stop_reason}")
            try:
                rep.save()
            except Exception:
                pass
        return result

    def report(self, plan: dict, started: float) -> dict:
        final = self.draft if (self.dry_run or not (self.target and self.target.exists())) else Draft.load(self.target)
        first = self.steps[0].duration_before if self.steps else (final.declared_duration if final else 0.0)
        result = {
            "task": self.task,
            "goal": self.goal,
            "dry_run": self.dry_run,
            "ok": bool(self.steps) and all(s.ok for s in self.steps),
            "understanding": plan.get("理解"),
            "reasoning": {
                "evidence": plan.get("证据") or [],
                "assumptions": plan.get("假设") or [],
                "uncertainties": plan.get("不确定性") or [],
                "alternatives": plan.get("备选方案") or [],
                "choice_reason": plan.get("选择理由") or "",
                "stop_condition": plan.get("停止条件") or "",
            },
            "plan_source": plan.get("来源"),
            "warnings": plan.get("warnings") or [],
            "duration": {"start": round(first, 3),
                         "end": round(final.declared_duration, 3) if final else 0.0},
            "draft": self.target.name if self.target else None,
            "created": self.created,
            "steps": [{"skill": s.skill, "args": s.args, "ok": s.ok, "attempts": s.attempts,
                       "note": s.note, "snapshot": s.snapshot,
                       "duration": [s.duration_before, s.duration_after],
                       "checks": s.checks, "error": s.error,
                       "report": {k: v for k, v in s.report.items() if k in (
                           "rc", "error", "skipped", "note", "spans", "applied", "status",
                           "request_id", "request_sha256", "inspection", "selection",
                           "artifact", "report_path", "delivery", "ready_to_ship", "dry_run")}}
                       for s in self.steps],
            "seconds": round(time.time() - started, 1),
            "model_calls": llm_calls(),
        }
        if self.steps and (self.steps[-1].skill in {'inspect_video_plan', 'make_video'}
                           or self.steps[-1].report.get('request_id')
                           or self.steps[-1].report.get('stop_agent')):
            result['status'] = self.steps[-1].report.get('status', 'dry_run' if self.dry_run else 'blocked')
            result['production'] = result['steps'][-1]['report']
            result['ready_to_ship'] = False
        return result


def llm_calls() -> list[dict]:
    from . import llm

    return llm.last_calls()


def run_task(**kwargs) -> tuple["Agent", dict]:
    agent = Agent(**kwargs)
    return agent, agent.run()
