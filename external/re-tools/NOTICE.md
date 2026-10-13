# Local interoperability research references

AI CUT uses the already installed **pefile 2024.8.26** for read-only PE analysis.
Upstream: https://github.com/erocarrera/pefile (MIT).
Optional dependency is pinned in `requirements-re-tools.txt`. The PE reader does
not load the inspected binary. Set max_symbol_exports=200000: the default 8192
silently omits the relevant exports of this installed videoeditor.dll.

The MSVC string ABI and exported decrypt signature in `native_draft_worker.py`
were adapted from **wenshui330/jy-draftc** (MIT), fixed commit:
https://github.com/wenshui330/jy-draftc/tree/130aa03ab9e932d0f7062eec4b95d046a5985b97

Copyright (c) 2026 wenshui330. The complete MIT license is retained in
`jy-draftc/LICENSE`. Reference source is retained in `jy-draftc/reference.cpp`.
SHA-256: 5164edf7fa9cf44a6ff3ac618dde54fbba638644b6123a243a2aeb205323f1c7.
The reference C++ program is not compiled or executed by AI CUT. The independently
implemented Python worker exposes only local draft decoding in a separate process.

Discovery reference: https://github.com/zzz1999/jy-draft-port at commit
2cf5a0bee411249051f1ccf453b6f92e9e08828f. Its bundled upstream copy points to the
same jy-draftc commit. No JYDraftPort release executable was downloaded or run.

No Jianying executable/DLL, credentials, keys or third-party media are included.
The machine-local configuration pins a user-installed DLL by SHA-256 and is not a
portable compatibility guarantee. Other installations must validate their own DLL.
The upstream README reports versions 10.3.0 through 11.1.0; AI CUT separately
tested a local draft with the installed 11.5.0.14471 DLL on 2026-10-12.
