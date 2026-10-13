"""Visual-example retrieval and image-grounded reasoning for editing decisions.

The full paired-study corpus remains indexed by ``learning_context``. At edit
time, this module places representative source frames beside frames from the
most relevant studies and asks the local vision model for a grounded comparison.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .paths import ROOT, config

CELL_W = 512
IMAGE_H = 288
LABEL_H = 42
MAX_SOURCE_FRAMES = 4
MAX_STUDIES = 2


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _source_frames(profiles: list[dict], limit: int) -> list[dict]:
    candidates = []
    for row in profiles:
        proof = row.get("semantic_evidence") or {}
        image = proof.get("image")
        if not image or proof.get("status") not in {
                "automatic_visual_observation_unreviewed", "human_reviewed_visual_observation"}:
            continue
        path = Path(image)
        if not path.is_file():
            continue
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if proof.get('sha256') and proof['sha256'] != actual_hash:
            continue
        candidates.append({
            "path": path,
            "timestamp": proof.get("timestamp", row.get("t0")),
            "shot_id": row.get("shot_id", f"shot_{row.get('shot', '?')}"),
            "time_range": row.get("time_range", [row.get("t0"), row.get("t1")]),
            "description": row.get("content") or (row.get("理解") or {}).get("主体", ""),
            "action": (row.get("理解") or {}).get("动作", ""),
            "shot_size": row.get("shot_size", "unknown"),
            "analysis_status": row.get("_analysis_status", "unknown"),
            "sha256": actual_hash,
        })
    if len(candidates) <= limit:
        return candidates
    # Evenly cover the source timeline rather than selecting adjacent shots.
    indexes = [round(i * (len(candidates) - 1) / (limit - 1)) for i in range(limit)] if limit > 1 else [len(candidates) // 2]
    return [candidates[i] for i in dict.fromkeys(indexes)]


def _reference_rows(evidence: list[dict], root: Path, limit: int) -> list[dict]:
    rows = []
    for doc in evidence:
        if doc.get("category") != "paired_visual_study":
            continue
        frames = doc.get("frames") or []
        observations = {o.get("frame_sha256"): o for o in doc.get("frame_observations", [])}
        valid = []
        for frame in frames:
            frame_path = Path(frame["path"])
            image = frame_path.resolve() if frame_path.is_absolute() else (root / frame_path).resolve()
            obs = observations.get(frame.get("sha256"))
            if (image.is_relative_to(root.resolve()) and image.is_file()
                    and obs and obs.get("vision_analysis")
                    and hashlib.sha256(image.read_bytes()).hexdigest() == frame.get('sha256')):
                valid.append({
                    "path": image, "timestamp": frame.get("time_seconds"),
                    "sha256": frame.get("sha256"), "analysis": obs.get("vision_analysis", {}),
                })
        if not valid:
            continue
        rows.append({
            "id": doc["id"], "title": doc.get("title", ""),
            "visual_category": doc.get("visual_category", ""),
            "style_type": doc.get("style_type", ""),
            "source_url": doc.get("source_url", ""),
            "platform_scope": doc.get("platform_scope", "general_visual_study"),
            "video_sha256": doc.get("video_sha256", ""),
            "video_metadata": doc.get("video_metadata", {}),
            "learning_dimensions": doc.get("learning_dimensions", []),
            "frames": valid,
            "review_status": doc.get("review_status", "automatic_analysis_not_human_reviewed"),
        })
        if len(rows) >= limit:
            break
    return rows


def _compose_board(source: list[dict], references: list[dict], output: Path) -> list[dict]:
    if not source or not references:
        raise ValueError("visual comparison requires source frames and paired reference frames")
    rendered = []
    for row_index, src in enumerate(source):
        items = [("current", src["path"], f"C{row_index + 1} {float(src['timestamp'] or 0):.1f}s")]
        for ref_index, ref in enumerate(references, 1):
            frame = ref["frames"][min(row_index, len(ref["frames"]) - 1)]
            items.append((f"reference-{ref_index}", frame["path"],
                          f"R{ref_index} {float(frame['timestamp'] or 0):.1f}s"))
        row = []
        for role, path, title in items:
            with Image.open(path) as opened:
                image = opened.convert("RGB")
                original_dimensions = list(image.size)
                image.thumbnail((CELL_W, IMAGE_H), Image.Resampling.LANCZOS)
                image = image.copy()
            row.append({"role": role, "title": title, "image": image,
                        "source_dimensions": original_dimensions,
                        "aspect_ratio": round(original_dimensions[0] / original_dimensions[1], 4)})
        rendered.append(row)
    column_widths = [max(row[column]["image"].width for row in rendered)
                     for column in range(1 + len(references))]
    row_height = IMAGE_H + LABEL_H
    canvas = Image.new("RGB", (sum(column_widths), row_height * len(source)), "#282b30")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    manifest = []
    column_x = [sum(column_widths[:column]) for column in range(len(column_widths))]
    for row_index, src in enumerate(source):
        y = row_index * row_height
        for column, item in enumerate(rendered[row_index]):
            x, width = column_x[column], column_widths[column]
            draw.rectangle((x, y, x + width - 1, y + LABEL_H - 1),
                           fill="#14354f" if column == 0 else "#264e36")
            draw.text((3, y + 5), item["title"], fill="white", font=font)
            image = item["image"]
            left = x + (width - image.width) // 2
            top = y + LABEL_H + (IMAGE_H - image.height) // 2
            canvas.paste(image, (left, top))
            content_rect = [left, top, left + image.width - 1, top + image.height - 1]
            draw.rectangle(content_rect, outline="#ffb000", width=2)
            if column == 0:
                manifest.append({"panel": f"CURRENT {row_index + 1}",
                                 **{k: v for k, v in src.items() if k != "path"},
                                 "source_image_dimensions": item["source_dimensions"],
                                 "source_image_aspect_ratio": item["aspect_ratio"],
                                 "content_rect_on_board": content_rect,
                                 "column_rect_on_board": [x, top, x + width - 1, top + image.height - 1]})
            else:
                ref = references[column - 1]
                frame = ref["frames"][min(row_index, len(ref["frames"]) - 1)]
                manifest.append({
                    "panel": f"REFERENCE {column}, row {row_index + 1}",
                    "study_id": ref["id"], "title": ref["title"],
                    "visual_category": ref["visual_category"], "style_type": ref["style_type"],
                    "source_url": ref["source_url"], "platform_scope": ref["platform_scope"],
                    "time_seconds": frame["timestamp"], "vision_analysis": frame["analysis"],
                    "review_status": ref["review_status"],
                    "source_image_dimensions": item["source_dimensions"],
                    "source_image_aspect_ratio": item["aspect_ratio"],
                    "content_rect_on_board": content_rect,
                    "column_rect_on_board": [x, top, x + width - 1, top + image.height - 1],
                })
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, "JPEG", quality=92, optimize=True)
    return manifest


def _compose_source_board(source: list[dict], output: Path) -> list[dict]:
    """Place current source frames side by side without inventing canvas space."""
    rendered = []
    for index, row in enumerate(source, 1):
        with Image.open(row['path']) as opened:
            image = opened.convert('RGB')
            dimensions = list(image.size)
            image.thumbnail((CELL_W, IMAGE_H), Image.Resampling.LANCZOS)
            image = image.copy()
        rendered.append((row, index, dimensions, image))
    widths = [entry[3].width for entry in rendered]
    x_positions = [sum(widths[:i]) for i in range(len(widths))]
    canvas = Image.new('RGB', (sum(widths), IMAGE_H + LABEL_H), '#242830')
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    panels = []
    for (row, index, dimensions, image), x in zip(rendered, x_positions):
        draw.rectangle((x, 0, x + image.width - 1, LABEL_H - 1), fill='#14354f')
        draw.text((3, 5), f"C{index} {float(row['timestamp'] or 0):.1f}s", fill='white', font=font)
        canvas.paste(image, (x, LABEL_H))
        rect = [x, LABEL_H, x + image.width - 1, LABEL_H + image.height - 1]
        draw.rectangle(rect, outline='#ffb000', width=2)
        panels.append({'panel': f'CURRENT {index}', 'source_image_dimensions': dimensions,
                       'source_image_aspect_ratio': round(dimensions[0] / dimensions[1], 4),
                       'content_rect_on_board': rect, 'time_seconds': row.get('timestamp')})
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, 'JPEG', quality=92, optimize=True)
    return panels


def _apply_layout_constraints(analysis: dict, layout_rows: list[dict]) -> None:
    by_panel = {r['panel']: r for r in layout_rows}
    for observation in analysis.get('当前画面观察') or []:
        panel = observation.get('panel')
        check = by_panel.get(panel)
        if check:
            observation['可见事实'] = check['原帧观察']
            observation['不确定项'] = list(dict.fromkeys(
                (observation.get('不确定项') or []) + (check.get('不确定项') or [])))
    occupied_terms = ('右侧', '左侧', '右手', '左手', '侧栏', '侧边', '侧方', '留白', '空白',
                      '负空间', '卡片', '分屏', '对角线', '手势周围', '手部周围', 'right side',
                      'left side', 'right hand', 'left hand', 'negative space')
    unsupported_asset_terms = ('插入', '实物', '图片', '配图', '图文列表', '衣物特写', '叠加素材', 'b-roll')
    revised = []
    for item in analysis.get('剪辑思考') or []:
        panel = item.get('镜头panel')
        check = by_panel.get(panel)
        suggestion = str(item.get('建议', '')).lower()
        has_side_advice = any(term in suggestion for term in occupied_terms)
        lacks_owned_assets = any(term in suggestion for term in unsupported_asset_terms)
        if check and ((check.get('独立侧栏可用') is False and has_side_advice) or lacks_owned_assets):
            item['建议'] = ('保持原始9:16全幅口播，不添加素材库里没有的衣物图片、实拍、侧栏或分屏。'
                           '只按逐字稿原句逐项更新短字幕，用简洁字级或关键词高亮突出原话里的数量和尺寸；'
                           '字幕位置结合当前镜头，避开脸部与手势。')
            item['理由'] = ('当前素材没有可供插入的衣物展示镜头；真实画面中的人物和场景也必须留在原构图内。'
                            '数量、尺寸和名称以原逐字稿为准。')
            item['适用性'] = '高'
            item['需验证'] = '逐字稿是否准确以及重点字幕是否遮挡当时的手势。'
        revised.append(item)
    covered_panels = {item.get('镜头panel') for item in revised}
    for observation in analysis.get('当前画面观察') or []:
        panel = observation.get('panel')
        if panel and panel not in covered_panels:
            revised.append({
                '镜头panel': panel,
                '建议': ('保持9:16全幅口播，只按逐字稿显示对应短字幕与已核实数量/尺寸；'
                         '不增加侧栏、卡片、分屏或未提供的衣物画面。字幕先复核是否避开脸部和手势。'),
                '理由': '该镜头没有单独通过布局复核，使用保守版式，避免遮挡主体或暗示不存在的画面素材。',
                '适用性': '中',
                '需验证': '人工预览本段原帧，确认字幕未覆盖脸部、手部及关键信息。',
            })
    analysis['剪辑思考'] = revised
    for comparison in analysis.get('相似样本比较') or []:
        if any(term in str(comparison.get('可迁移做法', '')).lower() for term in occupied_terms):
            comparison['可迁移做法'] = ('只迁移信息层级与主体避让原则。样本的左右位置关系不可直接套用；'
                                        '必须按本片原始画幅决定字幕位置。')
    if any(r.get('独立侧栏可用') is False for r in layout_rows):
        analysis['总体策略'] = (
            '以本片真实9:16全幅口播为主轴，不安排右侧清单卡片、侧栏或分屏。'
            '只按原逐字稿制作简洁短字幕；数量、尺寸与疑似ASR错词均以原声复核为准。'
            '字幕位置逐镜头检查，避开脸部和手势；不插入未提供的衣物实拍或图片。'
        )
    analysis['空间布局复核'] = {
        '方法': '本地视觉模型对当前素材抽样原帧的独立复核',
        '面板结果': layout_rows,
        '应用规则': '复核判定无独立侧栏空间时，清除依赖侧边留白的卡片/贴图建议。',
    }


def compare_source_to_memory(
    visual_profiles: list[dict] | None,
    learning_bundle: dict,
    *,
    goal: str = "",
    root: str | Path = ROOT,
    timeout: int = 900,
) -> dict:
    """Visually compare current source shots with retrieved video-study frames.

    A successful result is a real local vision-model response over one board that
    contains both current frames and paired-study frames. Missing images or model
    failures remain explicit statuses; text descriptions are never presented as
    a visual comparison.
    """
    from .learning_context import selected_evidence
    source = _source_frames(visual_profiles or [], MAX_SOURCE_FRAMES)
    refs = _reference_rows(selected_evidence(learning_bundle), Path(root).resolve(), MAX_STUDIES)
    if not source:
        return {"status": "skipped_no_source_images", "model_saw_images": False,
                "reason": "当前镜头档案没有持久化的真实源帧图片。"}
    if not refs:
        return {"status": "skipped_no_retrieved_paired_studies", "model_saw_images": False,
                "reason": "本片检索结果中没有通过完整性校验的配对视频样本。"}

    from . import bundled_vision, llm
    use_bundled = bool((config().get('vision') or {}).get('bundled', False))
    model = bundled_vision.info()["id"] if use_bundled else llm.get_vision_model()
    backend = 'bundled' if use_bundled else 'ollama'
    identity = _stable_hash({
        "version": 7, "backend": backend, "model": model, "goal": goal,
        "source": [{k: v for k, v in row.items() if k != "path"} for row in source],
        "references": [{k: v for k, v in row.items() if k != "frames"} | {
            "frames": [{k: v for k, v in f.items() if k != "path"} for f in row["frames"]]
        } for row in refs],
    })
    work = Path(root).resolve() / "work" / "visual-memory"
    board = work / "boards" / f"{identity}.jpg"
    result_path = work / "reasoning" / f"{identity}.json"
    if result_path.is_file():
        try:
            cached = json.loads(result_path.read_text(encoding="utf-8"))
            if (cached.get("status") == "analyzed" and cached.get("board_sha256")
                    == hashlib.sha256(board.read_bytes()).hexdigest()):
                cached["cached"] = True
                validate_analysis(cached.get('analysis') or {}, {r['id'] for r in refs},
                                  {f'CURRENT {i+1}' for i in range(len(source))})
                return cached
        except (OSError, ValueError):
            pass

    manifest = _compose_board(source, refs, board)
    manifest_path = board.with_suffix(".json")
    _write_json(manifest_path, {"identity": identity, "goal": goal,
                                "model": model, "panels": manifest,
                                "limitations": ["源帧与参考帧是抽样画面", "静帧不能单独证明镜头运动或剪辑顺序"]})
    prompt = (
        "你是剪辑导演，正在做‘当前素材’与‘视觉学习样本’的画面对照。学习样本仅用于提炼风格经验，不能当作本片可剪素材；"
        "画面相似不代表同一场景、同一人物或同一拍摄；除非视频素材身份与来源哈希相同，禁止建议把参考样本和当前素材无缝拼接。"
        "图片每一行的第一列是当前待剪素材真实画面；右侧每列是检索到的学习视频真实画面，"
        "同一列由上到下是同一参考视频的时间顺序抽帧。面板编号和逐帧文字分析如下：\n"
        + json.dumps({"制作目标": goal, "面板与来源": manifest}, ensure_ascii=False)
        + "\n样本ID填写规则：相似样本比较的样本id必须从本轮真实来源清单中原样复制："
        + json.dumps([{"样本id": r['id'], "标题": r['title']} for r in refs], ensure_ascii=False)
        + "。禁止填标题、类别、CURRENT编号、推测ID或清单之外的值。当前镜头必须原样引用CURRENT面板编号。每个比较只选一个清单内样本。"
        + "\n对照图各列按源图原始宽高比排版；面板元数据列出source_image_dimensions、source_image_aspect_ratio与content_rect_on_board。"
        "如果同列源帧画幅不同，橙框外可能有版面空位，它不属于原视频。"
        "禁止把列边距当作原视频留白、背景、负空间或字幕安全区；禁止据此说主体偏左/偏右或建议在那里加字。"
        "仅依据对应面板内的原始画面像素描述主体位置、构图和可用空间。"
        + "\n请先依据可见像素指出当前素材的主体、景别、构图、光色、动作状态和画面限制，"
        "再比较最相近的学习样本，推理可迁移到本片的剪辑思路。输出 JSON："
        '{"当前画面观察":[{"panel":"CURRENT 1","可见事实":"...","不确定项":[]}],'
        '"相似样本比较":[{"当前镜头":"CURRENT 1","样本id":"...","相似点":"...",'
        '"差异点":"...","可迁移做法":"...","不应照搬":"..."}],'
        '"剪辑思考":[{"镜头panel":"CURRENT 1","建议":"...","理由":"结合本片可见画面和制作目标",'
        '"适用性":"高/中/低","需验证":"..."}],'
        '"总体策略":"...","不确定项":[]}。'
        "不要只复述旁边文字；若图像与文字不符，以可见画面为准并指出冲突。"
        "不得把单帧推断成连续运动或已验证的剪辑顺序。不得复制参考作品的独特构图、文字或贴图。"
        "只输出合法 JSON。"
    )
    analysis = None
    image_request_returned = False
    try:
        if use_bundled:
            analysis = bundled_vision.analyze_frame(board, prompt, max_tokens=2400,
                                                   temperature=0.1, timeout=timeout)
        else:
            analysis = llm.vision_json(board, prompt, provider='ollama', model=model,
                                       max_tokens=2400, timeout=timeout)
        image_request_returned = True
        analysis.pop("_image", None)
        if analysis.get("error"):
            raise RuntimeError(str(analysis["error"]))
        validate_analysis(analysis, {r['id'] for r in refs},
                          {f'CURRENT {i+1}' for i in range(len(source))})
        layout_rows = []
        layout_cache_path = work / "layout-observations.json"
        try:
            layout_cache_data = json.loads(layout_cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            layout_cache_data = {"schema_version": 1, "observations": {}}
        layout_cache = layout_cache_data.get("observations") or {}
        for source_index, frame in enumerate(source, 1):
            layout_prompt = (
                "你是竖屏短视频的字幕与贴图安全区审校员。你看到的是一张未拼接、未留边的原始9:16视频帧。"
                "直接检查完整画面中人物躯干、头部和手臂的边界；分别描述左半与右半实际包含的像素。"
                "判断右侧25%能否放一张独立的清单卡片（至少占画面高度25%），要求卡片能避开人物及肢体，"
                "且不遮挡重要的场景内容。窗户、墙、沙发等可见实景虽然不是人物，也不等同于干净纯色留白。"
                "若人体宽度超过画面55%或越过画面中心，右侧卡片必须判false。"
                "不确定时判false。坐标归一化，格式[x1,y1,x2,y2]。"
                "只输出JSON：{\"原帧观察\":\"直接可见事实\",\"人物框归一化估计\":[0,0,1,1],"
                "\"右侧画面组成\":\"人物肢体及实景\",\"独立侧栏可用\":false,"
                "\"证据\":\"引用可见的人物边界和背景细节\",\"不确定项\":[]}。"
            )
            try:
                cached_layout = layout_cache.get(frame['sha256'])
                if (isinstance(cached_layout, dict) and cached_layout.get('model') == model
                        and cached_layout.get('review_status') == 'automatic_analysis_not_human_reviewed'):
                    layout_result = {
                        '原帧观察': cached_layout.get('原帧观察', ''),
                        '人物框归一化估计': cached_layout.get('人物框归一化估计'),
                        '独立侧栏可用': cached_layout.get('独立侧栏可用'),
                        '证据': cached_layout.get('证据', ''),
                        '不确定项': cached_layout.get('不确定项') or [],
                        'cached_by_frame_sha256': True,
                    }
                elif use_bundled:
                    layout_result = bundled_vision.analyze_frame(frame['path'], layout_prompt,
                                                                 max_tokens=500, temperature=0.1,
                                                                 timeout=timeout)
                else:
                    layout_result = llm.vision_json(frame['path'], layout_prompt, provider='ollama', model=model,
                                                    max_tokens=500, timeout=timeout)
                box = layout_result.get('人物框归一化估计') or []
                if (not str(layout_result.get('原帧观察') or '').strip()
                        or not str(layout_result.get('证据') or '').strip()
                        or type(layout_result.get('独立侧栏可用')) is not bool
                        or len(box) != 4
                        or any(not isinstance(v, (int, float)) or not 0 <= v <= 1 for v in box)):
                    raise ValueError('输出字段不完整')
                # A centered/wide person overlaps the right column even if the
                # backdrop there is a window. Do not trust layout advice from a composite.
                if box[2] - box[0] > 0.55 and box[0] < 0.5 < box[2]:
                    layout_result['独立侧栏可用'] = False
                    layout_result['证据'] += ' 人物框跨越画面中心且横向宽度较大，按安全规则不安排侧栏卡片。'
                if not layout_result.get('cached_by_frame_sha256'):
                    layout_cache[frame['sha256']] = {
                        'model': model,
                        'review_status': 'automatic_analysis_not_human_reviewed',
                        '原帧观察': layout_result['原帧观察'],
                        '人物框归一化估计': box,
                        '独立侧栏可用': layout_result['独立侧栏可用'],
                        '证据': layout_result['证据'],
                        '不确定项': layout_result.get('不确定项') or [],
                    }
                    _write_json(layout_cache_path, {"schema_version": 1, "observations": layout_cache})
                layout_result.pop('_provider', None)
                layout_rows.append({'panel': f'CURRENT {source_index}', **layout_result})
            except Exception as exc:  # noqa: BLE001
                # A layout-check formatting/timeout failure must not discard a
                # completed source-to-reference visual comparison. Fail closed:
                # keep the whole frame and prohibit independent side cards.
                observed = next((r.get('可见事实', '') for r in analysis.get('当前画面观察', [])
                                 if r.get('panel') == f'CURRENT {source_index}'), '')
                layout_rows.append({
                    'panel': f'CURRENT {source_index}',
                    '原帧观察': (str(observed).strip() + '；原始帧布局复核未完成，按全幅口播处理。').strip('；'),
                    '人物框归一化估计': [0, 0, 1, 1],
                    '独立侧栏可用': False,
                    '证据': f'布局模型复核失败，保守禁用侧栏/卡片/分屏：{type(exc).__name__}: {str(exc)[:180]}',
                    '不确定项': ['本帧空间布局未通过独立原帧复核'],
                    'failed_closed': True,
                })
        _apply_layout_constraints(analysis, layout_rows)
        validate_analysis(analysis, {r['id'] for r in refs},
                          {f'CURRENT {i+1}' for i in range(len(source))})
        result = {
            "status": "analyzed", "model_saw_images": True, "model": model, "backend": backend,
            "layout_status": "partial_fail_closed" if any(r.get('failed_closed') for r in layout_rows) else "verified",
            "identity": identity, "board_path": str(board),
            "board_sha256": hashlib.sha256(board.read_bytes()).hexdigest(),
            "manifest_path": str(manifest_path), "source_frames": len(source),
            "source_layout_model": model,
            "reference_studies": [{"id": r["id"], "title": r["title"],
                                    "source_url": r["source_url"],
                                    "platform_scope": r["platform_scope"],
                                    "video_sha256": r["video_sha256"],
                                    "review_status": r["review_status"]} for r in refs],
            "analysis": analysis,
            "limitations": ["当前素材与样本各使用抽样帧", "静帧不能单独证明镜头运动或剪辑顺序",
                            "视觉分析自动生成，仍需人工复核"],
            "cached": False,
        }
        _write_json(result_path, result)
        return result
    except Exception as exc:  # noqa: BLE001
        result = {"status": "vision_comparison_failed", "model_saw_images": image_request_returned,
                "model": model, "identity": identity, "board_path": str(board),
                "manifest_path": str(manifest_path), "error": f"{type(exc).__name__}: {str(exc)[:400]}",
                "reference_studies": [{"id": r["id"], "title": r["title"]} for r in refs]}
        if image_request_returned and isinstance(analysis, dict):
            result['analysis'] = analysis
        _write_json(result_path, result)
        return result


def validate_analysis(analysis: dict, study_ids: set[str], panels: set[str]) -> None:
    """Require grounded observations, real study bindings and actionable advice."""
    observations = analysis.get('当前画面观察') or []
    comparisons = analysis.get('相似样本比较') or []
    ideas = analysis.get('剪辑思考') or []
    if not observations or not all(isinstance(r, dict) and r.get('panel') in panels and str(r.get('可见事实') or '').strip()
               for r in observations):
        raise ValueError('视觉对照缺少有效当前画面观察')
    if not comparisons or not all(isinstance(r, dict) and r.get('样本id') in study_ids
               and r.get('当前镜头') in panels and str(r.get('可迁移做法') or '').strip() for r in comparisons):
        found = sorted({str(r.get('样本id')) for r in comparisons if isinstance(r, dict)})
        raise ValueError(f'视觉对照缺少真实样本与当前镜头的对应比较；允许样本={sorted(study_ids)}；模型样本={found}')
    if not ideas or not all(isinstance(r, dict) and r.get('镜头panel') in panels
               and str(r.get('建议') or '').strip() and str(r.get('理由') or '').strip() for r in ideas):
        raise ValueError('视觉对照缺少有画面依据的剪辑建议')
