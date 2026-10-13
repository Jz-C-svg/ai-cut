"""Extract local OCR/layout observations from still images in the account export."""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def collect(dataset: Path, runtime: Path) -> dict:
    sys.path.insert(0, str(runtime))
    from rapidocr_onnxruntime import RapidOCR
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8-sig"))
    ocr = RapidOCR(det_model_path=None, det_limit_side_len=1280, det_limit_type="max", use_angle_cls=False)
    output = dataset / "production-visual" / "image-text"
    output.mkdir(parents=True, exist_ok=True)
    target = output / "image-text-index.jsonl"
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8-sig").splitlines() if line.strip()] if target.exists() else []
    completed = {row.get("source_sha256") for row in rows}
    errors = []
    for row in manifest:
        if row.get("kind") != "image" or row.get("sha256") in completed:
            continue
        path = Path(row["file"])
        try:
            data = np.fromfile(path, dtype=np.uint8)
            image = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError("image decode failed")
            h, w = image.shape[:2]
            scale = min(1.0, 1280 / max(h, w))
            if scale < 1.0:
                image = cv2.resize(image, (max(1, round(w*scale)), max(1, round(h*scale))), interpolation=cv2.INTER_AREA)
            predictions, _ = ocr(image)
            blocks = []
            for box, text, score in predictions or []:
                points = np.asarray(box, dtype=float)
                x0, y0 = points.min(axis=0)
                x1, y1 = points.max(axis=0)
                blocks.append({"text": str(text), "confidence": float(score),
                               "bbox_normalized": [float(x0/w), float(y0/h), float(x1/w), float(y1/h)],
                               "vertical_zone": "upper" if (y0+y1)/2 < h/3 else "lower" if (y0+y1)/2 > h*2/3 else "middle",
                               "large_text_candidate": bool((y1-y0)/h >= .045),
                               "review_status": "automatic_ocr_unreviewed"})
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            quant = (image // 32) * 32 + 16
            colors, counts = np.unique(quant.reshape(-1, 3), axis=0, return_counts=True)
            palette = [{"bgr": color.tolist(), "fraction": float(count/counts.sum())}
                       for color, count in sorted(zip(colors, counts), key=lambda item: -item[1])[:6]]
            rows.append({"source": str(path), "relative": row["relative"], "source_sha256": row["sha256"],
                         "width": w, "height": h, "ocr_model": "RapidOCR PP-OCRv3 local CPU",
                         "text_blocks": blocks, "dominant_image_colors_bgr": palette,
                         "mean_luma": float(gray.mean()), "text_review_status": "automatic_unreviewed",
                         "semantic_image_description": None})
            completed.add(row["sha256"])
        except Exception as exc:
            errors.append({"source": str(path), "error": str(exc)[:500]})
        if len(rows) % 5 == 0 or row == manifest[-1]:
            target.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rows), encoding="utf-8")
            print("image OCR {}/{}".format(len(rows), sum(item.get("kind") == "image" for item in manifest)), flush=True)
    target.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    summary = {"images_expected": sum(row.get("kind") == "image" for row in manifest),
               "images_processed": len(rows), "ocr_blocks": sum(len(row["text_blocks"]) for row in rows),
               "errors": errors, "human_labels": 0, "semantic_image_description_performed": False,
               "source_images_modified": False, "index": str(target)}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    p.add_argument("--runtime", default=str(ROOT / "work/dataset-ocr-runtime"))
    args = p.parse_args()
    print(json.dumps(collect(Path(args.dataset), Path(args.runtime)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
