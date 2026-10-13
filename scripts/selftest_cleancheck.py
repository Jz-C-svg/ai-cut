# -*- coding: utf-8 -*-
"""画面干净检查 + before/after 比对 自测（selftest_cleancheck）

覆盖：
  1. cleancheck.check_frame / check_image_file：五维分数与干净度分级
  2. compare.compare_frames：生成并排比对图 + 差异框 + 指标
  3. critique_loop.critique：cleancheck 注入（video 可用时返回 cleancheck 字段）
  4. export_report：set_quality_result + generate 含"五、画面质量验证"章节
"""
import sys as _portable_sys
from pathlib import Path as _PortablePath
_portable_sys.path.insert(0, str(_PortablePath(__file__).resolve().parents[1] / "lib"))
from aicut.portability import portable_path
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import cv2
import numpy as np

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    mark = "PASS" if cond else "FAIL"
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(f"[{mark}] {name}" + (f" ｜ {detail}" if detail else ""))


def _real_frame(sec=1.0):
    """从真实素材抽帧（测试基准：真实口播画面）"""
    vid = portable_path('project:work/normalized/IMG_5845.mp4')
    cap = cv2.VideoCapture(vid)
    if not cap.isOpened():
        raise FileNotFoundError(vid)
    cap.set(cv2.CAP_PROP_POS_MSEC, sec * 1000.0)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(f"抽帧失败 {vid}@{sec}s")
    return frame


def _dirty_frame(base):
    """在原帧上制造脏画面：加噪 + 底部字幕条带 + 角落红块水印"""
    img = base.copy()
    rng = np.random.default_rng(11)
    noise = rng.normal(0, 28, img.shape[:2]).astype(np.float32)
    img = np.clip(img.astype(np.float32) + noise[..., None], 0, 255).astype(np.uint8)
    h, w = img.shape[:2]
    cv2.rectangle(img, (0, int(h * 0.86)), (w, h), (18, 18, 18), -1)   # 底部字幕黑条
    cv2.rectangle(img, (int(w * 0.86), int(h * 0.05)), (w, int(h * 0.18)),
                  (60, 60, 220), -1)                                   # 角落红色水印
    cv2.putText(img, "测试字幕", (int(w * 0.32), int(h * 0.92)),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)
    return img


def main():
    print("===== selftest_cleancheck：画面干净检查 + 比对 =====")

    # 1) 真实素材帧 vs 人工脏化帧
    from aicut import cleancheck
    base = _real_frame()
    dirty = _dirty_frame(base)
    r_clean = cleancheck.check_frame(base)
    r_noisy = cleancheck.check_frame(dirty)
    check("真实帧评分 0-100", 0 <= r_clean["clean_score"] <= 100,
          f"score={r_clean['clean_score']} level={r_clean['level']}")
    check("真实帧五维齐全", set(r_clean["dims"]) == {"clutter", "ghost", "subject",
                                                       "subtitle", "corner"})
    check("真实帧干净度 >= 60（真实口播画面不应过低）",
          r_clean["clean_score"] >= 60, f"score={r_clean['clean_score']}")
    check("脏化帧分数低于原帧", r_noisy["clean_score"] < r_clean["clean_score"],
          f"dirty={r_noisy['clean_score']} vs clean={r_clean['clean_score']}")
    check("脏化帧检测到问题", len(r_noisy["issues"]) > 0, str(r_noisy["issues"]))

    # 2) compare 生成比对图
    from aicut import compare
    tmp = tempfile.mkdtemp(prefix="aicut_clean_")
    out = str(Path(tmp) / "compare_test.png")
    cmp = compare.compare_frames(base, dirty, out_path=out)
    check("比对图已生成", Path(out).exists(), out)
    check("比对指标齐全", "before_metrics" in cmp and "after_metrics" in cmp)
    check("差异框数 >= 1", cmp["diff_count"] >= 1, f"diff={cmp['diff_count']}")

    # 3) critique 注入 cleancheck（真实草稿 + 真实素材）
    try:
        from aicut import critique_loop
        draft_dir = portable_path('project:06-drafts/真实素材测试_20260911')
        vid = portable_path('project:work/normalized/IMG_5845.mp4')
        rv = critique_loop.critique(draft_dir, video=vid, use_vision=False)
        check("critique 返回 cleancheck 字段", "cleancheck" in rv,
              (f"score={rv['cleancheck']['clean_score']}" if rv.get("cleancheck") else "None"))
        check("critique 可正常运行", rv.get("overall_score", -1) >= 0,
              f"overall={rv.get('overall_score')}")
    except Exception as e:
        check("critique 注入可运行", False, str(e)[:150])

    # 4) export_report 质量章节
    try:
        from aicut.export_report import ExportReport
        rep = ExportReport("selftest_quality", output_dir=tmp)
        rep.set_materials([])
        rep.set_quality_result(clean=r_clean, compare=cmp)
        text = rep.generate("md")
        check("报告含质量章节", "## 五、画面质量验证" in text)
        check("报告含干净度分数", "干净度评分" in text)
        check("报告含比对信息", "before/after 比对" in text)
    except Exception as e:
        check("export_report 质量章节", False, str(e)[:150])

    print(f"\n结果：PASS {PASS} / FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
