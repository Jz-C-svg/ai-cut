# AI CUT

[中文](README.md) · [Open-source scope](docs/OPEN_SOURCE_SCOPE.md) · [Contributing](CONTRIBUTING.md)

AI CUT is an early-stage, Windows-oriented project for AI-assisted video editing. It connects media analysis, transcription, editing plans, production requests, and artifact checks. A model proposes a plan; explicit production requests execute the supported work.

**This release contains development source, not a fully accepted one-command editing product.** Clean-machine deployment, complete visible editor operation and export, and aesthetic quality still require validation. A generated project does not prove the editor ran it; a decodable MP4 does not prove the edit looks good.

## Try the standalone demo first

The local workbench uses Python's standard library and requires FFmpeg / ffprobe on `PATH` (FFmpeg must include libass). From the repository root:

```powershell
python demo/server.py
```

Open <http://127.0.0.1:8766>. Offline mode uses original AI coffee imagery and a fixed plan to demonstrate approval, FFmpeg rendering and full decoding. You can supply your own footage and manual plan. **The fixed plan is not fresh model generation; the demo does not operate Jianying.** Cloud analysis is disabled by default, requires your own credentials and explicit consent to send sampled frames and text, and needs independent quality/cost validation. See the [demo setup and asset provenance](demo/README.md).

Six standalone demo checks passed in this release preparation. They do not cover live cloud inference or autonomous editor operation. The demo is the preferred runnable entry point in this source release.

## Workflow

```text
Your footage → ASR / frame analysis → editing plan → inspection
                                                       ↓ approval
                                          Jianying draft / FFmpeg MP4
                                                       ↓
                                         readback, checks, visual review
```

This is the main project's intended workflow. Some production paths in this public copy depend on excluded components; see the limits below.

- CLI, Python SDK, and MCP share a production interface that separates inspection from execution.
- Plans, input hashes, resumable host tasks, and export checks help preserve provenance.
- Effects, filters, subtitles, and keyframes have structured execution and readback checks. Resource availability, editor versions, and appearance need separate validation.
- Retrieval can inform planning; retrieval-augmented generation (RAG) is not model training or fine-tuning. The original collected databases are excluded.

## Support status

| Path | Included scope | Remaining validation |
| --- | --- | --- |
| Standalone demo | Local workbench without Python third-party dependencies; fixed/manual plans and approved FFmpeg rendering | Live cloud results, production deployment and aesthetic acceptance |
| Jianying Pro (CapCut CN) | Draft generation, production requests, host steps and readback; writing requires external `JianYingDraft` | Clean-machine compatibility, complete visible operation through native export, aesthetic acceptance |
| CapCut | Draft adapter code | No completed live-editor acceptance; parity with Jianying is not promised |
| Premiere / DaVinci Resolve / Final Cut / VEGAS / After Effects | Bridge or interface code | Unaccepted native delivery paths are blocked by the production entry point |
| Main-project portable / FFmpeg MP4 | Rendering and technical-check code retained | Still indirectly imports `build_draft`, which requires excluded `JianYingDraft`; end-to-end execution is blocked pending decoupling. The standalone demo has no such dependency |
| Read-only native project analysis | PE inspection, draft probing, version-bound local readback | No universal decoder; omitted defaults, frame quantization and curve semantics remain compatibility gaps |

Host tasks return pending steps and accept real observation records. Creating a task does not prove visible import, placement, cutting, or export occurred. Missing dependencies, unavailable models, unsupported plans, and failed checks must retain their blocked status.

## Getting started from source

Use PowerShell from the repository root. Python 3.12 is suggested; desktop automation targets Windows. Install FFmpeg and ffprobe separately and make them available on `PATH`. Model weights, editor software, FFmpeg binaries, and `JianYingDraft` are not bundled.

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt

# Inspect tool schemas; no media access, model inference, or rendering
.\venv\Scripts\python.exe -X utf8 scripts\maker_api.py tools
```

Review `config/aicut.json` for your FFmpeg paths, ASR, model backends, and editor draft location. Child processes default to the currently running Python; the `python` setting can override it. Do not copy private machine paths or credentials into tracked configuration. Review `requirements-optional.txt` for the modules you need; the base installation does not include every ASR, vision, GUI or automation dependency.

For Jianying draft generation, obtain and install a compatible `JianYingDraft` separately and verify that the virtual environment can `import JianYingDraft`. This repository does not bundle or relicense that dependency, or guarantee compatibility with every similarly named package.

The following is a main-project development example. It requires compatible external dependencies, accessible media and an available model first; without them it is blocked, not an out-of-the-box rendering example:

```powershell
.\venv\Scripts\python.exe -X utf8 scripts\agent.py --material "<your-footage.mp4>" --goal "Keep the original speech; segment subtitles by spoken phrases; prepare a talking-head edit" --software jianying --engine project

# Review the plan, inputs and destination, then supply its request ID and approval hash
.\venv\Scripts\python.exe -X utf8 scripts\maker_api.py execute --request "<request_id>" --approve-request-sha256 "<request_sha256>"
```

`portable` selects the main project's FFmpeg interface, but its indirect `JianYingDraft` dependency currently prevents independent end-to-end execution in this public copy. Use the standalone demo to try rendering now. Select `--copy-to-editor` explicitly during inspection to request copying into the editor library; back up existing drafts first. Request inspection of an existing sheet with `scripts/maker_api.py inspect --sheet "<plan.sheet.json>"`; the selected path's dependencies still apply, and this is not fresh model analysis.

`scripts/mcp_server.py` exposes a stdio MCP entry point for an external agent. The host is responsible for running the workflow, approving local tools, and supplying window automation. A Skill supplies instructions, not a running model or desktop permissions.

## Models and privacy

Local transcription and local models can support offline processing; installing packages and downloading models usually requires network access. Enabled cloud features may send text, sampled frames, or media required by the selected function. Online research sends queries; Edge-TTS sends synthesis text. Review the selected backend and actual features before processing sensitive footage.

Use environment variables and configuration placeholders for API keys. Do not commit `.env`, local configuration, logs, or generated output. Runtime reports may contain media paths, transcripts, and task details.

## Development and validation

Run relevant `scripts/selftest_*.py` checks for your change, for example:

```powershell
.\venv\Scripts\python.exe -X utf8 scripts\selftest_host_tasks.py
.\venv\Scripts\python.exe -X utf8 scripts\selftest_re_tools.py
```

Run from the repository root. Some historical tests depend on excluded media, folders, editor resources, or third-party libraries. Original-machine results are not clean-machine acceptance for this public copy. Include editor, Python, model and FFmpeg versions with a sanitized reproduction when reporting issues.

## License

Original project code and documentation are licensed under [Apache-2.0](LICENSE). Copyright 2026 AI CUT contributors. Third-party components retain their own licenses. Editor software, trademarks, and proprietary resources are outside this grant. AI CUT is not affiliated with their vendors. See the [open-source scope](docs/OPEN_SOURCE_SCOPE.md).
