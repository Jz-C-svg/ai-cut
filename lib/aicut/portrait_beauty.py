"""Tracked, face-local portrait smoothing with audio-preserving FFmpeg output."""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def soften_face(frame: np.ndarray, face: np.ndarray, strength: float = 0.65) -> np.ndarray:
    """Gently lift/smooth the face oval while masking eyes and mouth landmarks.

    ``face`` is YuNet's 15-value row scaled into full-frame pixel coordinates.
    The original pixels remain unchanged outside the feathered face mask.
    """
    if not 0.0 <= strength <= 1.0:
        raise ValueError("portrait beauty strength must be in 0..1")
    x, y, fw, fh = map(float, face[:4])
    if fw < 12 or fh < 12:
        return frame
    h, w = frame.shape[:2]
    x0 = max(0, int(x - fw * 0.08)); y0 = max(0, int(y - fh * 0.12))
    x1 = min(w, int(x + fw * 1.08)); y1 = min(h, int(y + fh * 1.05))
    roi = frame[y0:y1, x0:x1]
    if roi.size == 0:
        return frame

    smooth = cv2.bilateralFilter(roi, 9, 42, 28)
    lab = cv2.cvtColor(smooth, cv2.COLOR_BGR2LAB)
    light, a_chan, b_chan = cv2.split(lab)
    light = np.clip(light.astype(np.float32) + 6.0 * strength, 0, 255).astype(np.uint8)
    smooth = cv2.cvtColor(cv2.merge((light, a_chan, b_chan)), cv2.COLOR_LAB2BGR)

    rh, rw = roi.shape[:2]
    yy, xx = np.mgrid[:rh, :rw]
    mask = (((xx + x0 - (x + fw * 0.5)) / (fw * 0.50)) ** 2
            + ((yy + y0 - (y + fh * 0.51)) / (fh * 0.52)) ** 2 <= 1).astype(np.float32)

    # YuNet landmarks: right/left eye at 4/6, mouth corners at 10/12.
    for landmark_index, rx, ry in ((4, 0.13, 0.10), (6, 0.13, 0.10),
                                   (10, 0.20, 0.11), (12, 0.20, 0.11)):
        if len(face) <= landmark_index + 1:
            continue
        px, py = float(face[landmark_index]), float(face[landmark_index + 1])
        feature = (((xx + x0 - px) / (fw * rx)) ** 2
                   + ((yy + y0 - py) / (fh * ry)) ** 2) <= 1
        mask[feature] = 0

    sigma = max(2.0, rw * 0.035)
    mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=sigma)
    alpha = mask[:, :, None] * min(0.48, 0.58 * strength)
    frame[y0:y1, x0:x1] = np.clip(
        roi.astype(np.float32) * (1.0 - alpha) + smooth.astype(np.float32) * alpha,
        0, 255,
    ).astype(np.uint8)
    return frame



def whiten_and_slim_face(frame: np.ndarray, face: np.ndarray,
                         whiten_strength: float = 0.75,
                         slim_strength: float = 0.65) -> np.ndarray:
    """Apply a visible but restrained skin lift and cheek-edge slimming."""
    if not 0.0 <= whiten_strength <= 1.0 or not 0.0 <= slim_strength <= 1.0:
        raise ValueError("whiten/slim strength must be in 0..1")
    h, w = frame.shape[:2]
    x, y, fw, fh = map(float, face[:4])
    if fw < 12 or fh < 12:
        return frame
    x0=max(0,int(x-fw*.08)); y0=max(0,int(y-fh*.12))
    x1=min(w,int(x+fw*1.08)); y1=min(h,int(y+fh*1.05))
    roi=frame[y0:y1,x0:x1]
    if roi.size == 0:
        return frame
    rh,rw=roi.shape[:2]
    yy,xx=np.mgrid[:rh,:rw].astype(np.float32)
    gx=xx+x0; gy=yy+y0
    cx=x+fw*.5; cy=y+fh*.51
    oval=(((gx-cx)/(fw*.50))**2+((gy-cy)/(fh*.52))**2<=1).astype(np.float32)
    mask=cv2.GaussianBlur(oval,(0,0),sigmaX=max(4.0,rw*.065))

    # Lift luminance and gently neutralize excess yellow; keep facial features intact.
    lab=cv2.cvtColor(roi,cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[:,:,0]=np.clip(lab[:,:,0]+20.0*whiten_strength,0,255)
    lab[:,:,1]=128.0+(lab[:,:,1]-128.0)*(1.0-0.07*whiten_strength)
    lab[:,:,2]=128.0+(lab[:,:,2]-128.0)*(1.0-0.10*whiten_strength)
    lifted=cv2.cvtColor(np.clip(lab,0,255).astype(np.uint8),cv2.COLOR_LAB2BGR)
    alpha=mask[:,:,None]*0.82
    bright=np.clip(roi.astype(np.float32)*(1-alpha)+lifted.astype(np.float32)*alpha,0,255).astype(np.uint8)

    # Inward displacement is restricted to outer cheek/jaw edges, away from eyes,
    # nose and mouth. YuNet supplies a face box and stable eye/mouth landmarks.
    xn=np.abs((gx-cx)/(fw*.5))
    edge=np.clip((xn-0.42)/0.50,0,1); edge=edge*edge*(3-2*edge)
    vy=(gy-y)/fh
    rise=np.clip((vy-.38)/.16,0,1); rise=rise*rise*(3-2*rise)
    fall=np.clip((.94-vy)/.18,0,1); fall=fall*fall*(3-2*fall)
    vertical=rise*fall
    displacement=np.sign(gx-cx)*(fw*.045*slim_strength)*edge*vertical
    map_x=xx+displacement
    map_y=yy
    warped=cv2.remap(bright,map_x.astype(np.float32),map_y.astype(np.float32),
                     interpolation=cv2.INTER_LINEAR,borderMode=cv2.BORDER_REFLECT101)
    frame[y0:y1,x0:x1]=np.clip(
        roi.astype(np.float32)*(1-mask[:,:,None])+warped.astype(np.float32)*mask[:,:,None],
        0,255).astype(np.uint8)
    return frame


def apply_portrait_beauty(
    source: str | Path,
    destination: str | Path,
    config: dict[str, Any],
    ffmpeg: str = "ffmpeg",
) -> dict[str, Any]:
    """Retouch a rendered MP4 frame-by-frame and copy its audio unchanged."""
    source, destination = Path(source), Path(destination)
    model = Path(config.get("model") or "")
    if not model.is_absolute():
        model = Path(__file__).resolve().parents[2] / model
    if not model.is_file():
        raise FileNotFoundError(f"YuNet face model not found: {model}")
    strength = float(config.get("strength", 0.65))
    if not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError("portrait beauty strength must be finite and in 0..1")
    detect_every = int(config.get("detect_every_frames", 8))
    if not 1 <= detect_every <= 60:
        raise ValueError("face detection interval must be 1..60 frames")
    confidence = float(config.get("confidence", 0.5))
    if not 0.1 <= confidence <= 0.99:
        raise ValueError("YuNet confidence must be in 0.1..0.99")

    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise RuntimeError(f"could not open rendered video: {source}")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
    if width < 64 or height < 64 or not math.isfinite(fps):
        cap.release()
        raise RuntimeError("invalid rendered video dimensions or frame rate")

    detect_width = int(config.get("detect_width", 540))
    detect_height = max(1, round(height * detect_width / width))
    detector = cv2.FaceDetectorYN_create(
        str(model), "", (detect_width, detect_height), confidence, 0.3, 5000
    )
    detector.setInputSize((detect_width, detect_height))
    frame_count = 0; detections = 0; scores: list[float] = []
    tracked: np.ndarray | None = None
    miss_count = 0

    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pixel_format", "bgr24", "-video_size", f"{width}x{height}",
        "-framerate", f"{fps:.8f}", "-i", "pipe:0", "-i", str(source),
        "-map", "0:v:0", "-map", "1:a?", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", str(int(config.get("crf", 19))), "-pix_fmt", "yuv420p", "-c:a", "copy",
        "-shortest", "-movflags", "+faststart", str(destination),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE)
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_count % detect_every == 0:
                small = cv2.resize(frame, (detect_width, detect_height), interpolation=cv2.INTER_AREA)
                _, faces = detector.detect(small)
                if faces is not None and len(faces):
                    # In a single-speaker portrait, keep the largest centrally located face.
                    candidates = faces.copy()
                    frame_center = width * 0.5
                    candidates[:, 0] *= width / detect_width
                    candidates[:, 1] *= height / detect_height
                    candidates[:, 2] *= width / detect_width
                    candidates[:, 3] *= height / detect_height
                    for k in (4, 6, 8, 10, 12):
                        candidates[:, k] *= width / detect_width
                        candidates[:, k + 1] *= height / detect_height
                    chosen = max(candidates, key=lambda row: row[2] * row[3]
                                 / (1.0 + abs((row[0] + row[2] / 2) - frame_center) / width))
                    detections += 1; scores.append(float(chosen[14])); miss_count = 0
                    tracked = chosen if tracked is None else 0.62 * tracked + 0.38 * chosen
                else:
                    miss_count += 1
                    if miss_count >= 3:
                        tracked = None
            if tracked is not None:
                if config.get("whiten_enabled") or config.get("slim_enabled"):
                    frame = whiten_and_slim_face(
                        frame, tracked,
                        float(config.get("whiten_strength", 0.75)) if config.get("whiten_enabled") else 0.0,
                        float(config.get("slim_strength", 0.65)) if config.get("slim_enabled") else 0.0,
                    )
                else:
                    frame = soften_face(frame, tracked, strength)
            assert process.stdin is not None
            process.stdin.write(frame.tobytes())
            frame_count += 1
            if frame_count % max(1, int(fps * 10)) == 0:
                print(f"[人脸美颜] {frame_count} 帧；检测到人脸 {detections} 次", flush=True)
    except BaseException:
        cap.release()
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        process.kill(); process.wait()
        raise
    finally:
        cap.release()
    if process.stdin and not process.stdin.closed:
        process.stdin.close()
    stderr = (process.stderr.read() if process.stderr else b"").decode("utf-8", "replace")
    code = process.wait()
    if code:
        raise RuntimeError(f"face-beauty FFmpeg failed ({code}): {stderr[-2000:]}")
    if frame_count < 1:
        raise RuntimeError("face-beauty pass produced no frames")
    if detections == 0:
        raise RuntimeError("YuNet did not detect a face; refusing to claim beauty was applied")
    return {
        "renderer": "opencv-yunet-face-local",
        "model": str(model),
        "strength": strength,
        "detection_interval_frames": detect_every,
        "frames_processed": frame_count,
        "detection_samples": detections,
        "mean_detection_confidence": round(sum(scores) / len(scores), 4),
        "audio_uploaded": False,
        "faces_changed_geometrically": bool(config.get("slim_enabled")),
        "whiten_enabled": bool(config.get("whiten_enabled")),
        "whiten_strength": float(config.get("whiten_strength", 0.0)),
        "slim_enabled": bool(config.get("slim_enabled")),
        "slim_strength": float(config.get("slim_strength", 0.0)),
    }
