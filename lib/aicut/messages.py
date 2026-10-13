r"""给用户看的固定提示语集中放这里。

好处：话术统一、可以单独改文案，也方便英文/中文界面切换时只动一个文件。
"""
from __future__ import annotations

SILENCE_ALL = ("整条素材几乎没有有效声音（很可能是纯画面 / B-roll），"
               "这不是停顿——已跳过停顿裁剪，避免把整片剪没")

SILENCE_NONE = "没有找到符合条件的停顿"

DRY_SHELL_SKIP = "演算：跳过外部脚本执行，真跑会调用流水线"

FOREGROUND_MISMATCH = "前台不是剪映，拒发按键/点击"
