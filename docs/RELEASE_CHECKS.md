# Source release checks — 2026-10-13

This early source release was prepared separately from the working project.
Private runtime data, collected datasets, credentials, model weights, editor
binaries and the unverified local JianYingDraft implementation are excluded.

Verified during preparation:

- Python syntax parsing of 371 copied main-project Python modules.
- Seven standard-library-only CLI/path checks with site-packages disabled.
- Skill metadata validation.
- Six standalone Demo tests: plan rejection, confirmation/review state, mock
  vision protocol, complete sample rendering, and portrait/landscape/silent media.
- Tracked-file review for credential patterns, private account identifiers,
  machine user directories, excluded runtime paths and unreviewed binaries.
- Apache-2.0 project license and retained MIT reference attribution.

The six Demo tests can be reproduced with FFmpeg/ffprobe (including libass) on
PATH, from `demo/`:

```powershell
python selftest_demo.py
python selftest_delivery.py
```

The tests execute real local media processing. The API tests use mocks and are
not evidence of a live cloud model's editing quality. No fresh-machine GUI,
autonomous editor operation, full main-project render or aesthetic acceptance
is claimed. The main portable path still has an indirect dependency on the
excluded JianYingDraft implementation; use the independent Demo for the
reproducible rendering path.
