"""程序化生成 AIcut 应用图标（深色 + 胶片/AI 元素），输出多尺寸 .ico。"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "assets"
OUT.mkdir(parents=True, exist_ok=True)


def make_canvas(size: int) -> Image.Image:
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 圆角背景，深蓝渐变
    pad = max(1, s // 32)
    radius = s // 6
    bg = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bg)
    # 简单竖向渐变
    for y in range(s):
        t = y / s
        r = int(0x1e + (0x00 - 0x1e) * t)
        g = int(0x1e + (0x78 - 0x1e) * t)
        b = int(0x1e + (0xd4 - 0x1e) * t)
        bd.line([(0, y), (s, y)], fill=(r, g, b, 255))
    # 圆角遮罩
    mask = Image.new("L", (s, s), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=255)
    img.paste(bg, (0, 0), mask)
    return img


def build(size: int) -> Image.Image:
    img = make_canvas(size)
    d = ImageDraw.Draw(img)
    # 胶片齿孔（左右两侧）
    hole = max(2, size // 18)
    for side in (0, 1):
        x = (size // 16) if side == 0 else (size - size // 16 - hole)
        for i in range(3):
            y = size // 5 + i * (size // 5)
            d.rounded_rectangle([x, y, x + hole, y + hole], radius=hole // 3,
                                fill=(255, 255, 255, 220))
    # 中央 "AI" 文字
    txt = "AI"
    try:
        font = ImageFont.truetype("msyhbd.ttc", int(size * 0.42))
    except Exception:
        font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), txt, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (size - tw) / 2 - bbox[0]
    ty = (size - th) / 2 - bbox[1] - size * 0.02
    d.text((tx, ty), txt, font=font, fill=(255, 255, 255, 255))
    return img


def main():
    base = build(256)
    ico_path = OUT / "app.ico"
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base.save(ico_path, format="ICO", sizes=sizes)
    base.save(OUT / "app_256.png", format="PNG")
    print("saved", ico_path, ico_path.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
