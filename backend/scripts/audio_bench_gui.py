"""Graphical front-end for the audio test bench.

A tiny standalone web app: open it in a browser, pick audio files from your PC for
whichever roles you want to compare (single phone, natural mix, studio_voice,
karaoke, party, mono down-mix), click **Run**, and the generated HTML report opens
right in the page.

Only the files you attach appear in the report. If you attach just a mono file and
a studio file, the report contains exactly those two — nothing else.

Run it (from backend/):

    python scripts/audio_bench_gui.py
    # then open http://127.0.0.1:8200  (it tries to open automatically)

This is a developer/QA tool and binds to localhost only.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

# Make `app...` importable when run directly, then load the bench module by path
# (works whether launched as `python scripts/audio_bench_gui.py` or `-m`).
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "audio_bench", Path(__file__).resolve().parent / "audio_bench.py"
)
assert _spec and _spec.loader
bench = importlib.util.module_from_spec(_spec)
# Register before exec so dataclasses can resolve `cls.__module__` in sys.modules.
sys.modules["audio_bench"] = bench
_spec.loader.exec_module(bench)  # noqa: E402

# Roles the user can attach a file to. (label, friendly name, help text)
ROLE_FIELDS = [
    ("single_phone", "Single phone (raw)", "One phone / one channel — the reference."),
    ("raw_phone_2", "Second phone (raw)", "Optional. Enables sync offset + drift checks."),
    ("natural", "Natural stereo mix", "final_mix_stereo.wav — true L/R separation."),
    ("studio_voice", "Studio Voice", "Enhanced: cleaner/fuller voice."),
    ("karaoke", "Singing / Karaoke", "Enhanced: vocal reverb + light echo."),
    ("party", "Party / Room", "Enhanced: wider + more room."),
    ("mono_downmix", "Mono down-mix", "A mono version, e.g. to compare against stereo."),
]
_VALID_LABELS = {lbl for lbl, _, _ in ROLE_FIELDS}

app = FastAPI(title="Audio Test Bench GUI")

# Each run writes into its own temp dir; we serve report.html + wavs back by token.
_RUNS: dict[str, Path] = {}


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Audio Test Bench</title>
<style>
 :root{color-scheme:light dark;}
 body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:0;background:#0f1419;color:#e6e6e6;}
 .wrap{max-width:1100px;margin:0 auto;padding:22px;}
 h1{font-size:20px;margin:0 0 4px;}
 p.sub{color:#9aa4af;margin:0 0 18px;font-size:13px;}
 .grid{display:grid;grid-template-columns:1fr;gap:10px;}
 .role{background:#1a212b;border:1px solid #2a3441;border-radius:10px;padding:12px 14px;
   display:flex;align-items:center;gap:12px;}
 .role .meta{flex:1;min-width:0;}
 .role .name{font-weight:600;font-size:14px;}
 .role .hint{color:#8b96a2;font-size:12px;}
 .role input[type=file]{font-size:12px;color:#cbd5e1;max-width:340px;}
 .picked{color:#34d399;font-size:12px;white-space:nowrap;}
 .opts{margin:14px 0;background:#1a212b;border:1px solid #2a3441;border-radius:10px;padding:12px 14px;font-size:13px;}
 .opts label{display:block;margin:4px 0;}
 .bar{display:flex;gap:10px;align-items:center;margin:16px 0;}
 button{background:#2563eb;color:#fff;border:0;border-radius:8px;padding:10px 18px;
   font-size:14px;font-weight:600;cursor:pointer;}
 button:disabled{opacity:.5;cursor:default;}
 .ghost{background:#374151;}
 #status{font-size:13px;color:#9aa4af;}
 .err{color:#f87171;}
 iframe{width:100%;height:78vh;border:1px solid #2a3441;border-radius:10px;background:#fff;margin-top:14px;}
 a.dl{color:#60a5fa;font-size:13px;}
</style></head><body><div class="wrap">
<h1>Audio Test Bench</h1>
<p class="sub">Pick audio files from your PC for the roles you want to compare, then Run.
Only attached files appear in the report. Compare any subset — even just two.</p>

<form id="f">
 <div class="grid">
  __ROLE_ROWS__
 </div>
 <div class="opts">
  <label><input type="checkbox" id="gen_enh" checked>
   Auto-generate <b>studio_voice / karaoke / party</b> from the natural mix
   (only used for roles you did <i>not</i> attach yourself).</label>
  <label><input type="checkbox" id="gen_mono" checked>
   Auto-generate <b>mono down-mix</b> from the natural mix if not attached.</label>
  <label>Loudness target for A/B files:
   <input type="number" id="lufs" value="-16" step="1" style="width:64px"> LUFS</label>
 </div>
 <div class="bar">
  <button type="submit" id="run">Run analysis</button>
  <button type="button" class="ghost" id="reset">Clear</button>
  <span id="status"></span>
 </div>
</form>

<div id="links"></div>
<iframe id="report" style="display:none"></iframe>

<script>
const form = document.getElementById('f');
const statusEl = document.getElementById('status');
const reportEl = document.getElementById('report');
const linksEl = document.getElementById('links');

document.querySelectorAll('input[type=file]').forEach(inp=>{
  inp.addEventListener('change',()=>{
    const tag = inp.parentElement.querySelector('.picked');
    tag.textContent = inp.files.length ? '✓ '+inp.files[0].name : '';
  });
});

document.getElementById('reset').onclick = ()=>{
  form.reset();
  document.querySelectorAll('.picked').forEach(t=>t.textContent='');
  reportEl.style.display='none'; linksEl.innerHTML=''; statusEl.textContent='';
};

form.addEventListener('submit', async (e)=>{
  e.preventDefault();
  const fd = new FormData();
  let count = 0;
  document.querySelectorAll('input[type=file]').forEach(inp=>{
    if(inp.files.length){ fd.append(inp.dataset.role, inp.files[0]); count++; }
  });
  if(count===0){ statusEl.innerHTML='<span class="err">Attach at least one file.</span>'; return; }
  fd.append('gen_enhanced', document.getElementById('gen_enh').checked);
  fd.append('gen_mono', document.getElementById('gen_mono').checked);
  fd.append('target_lufs', document.getElementById('lufs').value || '-16');

  document.getElementById('run').disabled = true;
  statusEl.textContent = 'Analysing… (decoding, metrics, plots)';
  reportEl.style.display='none'; linksEl.innerHTML='';
  try{
    const res = await fetch('/run', {method:'POST', body:fd});
    const data = await res.json();
    if(!res.ok){ throw new Error(data.detail || 'Run failed'); }
    statusEl.textContent = data.checks_failed===0
      ? `Done — ${data.checks_total} checks passed. Clips: ${data.clips.join(', ')}`
      : `Done — ${data.checks_failed}/${data.checks_total} checks FAILED. Clips: ${data.clips.join(', ')}`;
    reportEl.src = '/report/'+data.token+'?t='+Date.now();
    reportEl.style.display='block';
    let links = '<p>Normalised A/B files: ';
    links += data.exports.map(f=>`<a class="dl" href="/file/${data.token}/${encodeURIComponent(f)}" download>${f}</a>`).join(' · ');
    links += `</p><p><a class="dl" href="/report/${data.token}" target="_blank">Open report in new tab</a></p>`;
    linksEl.innerHTML = links;
  }catch(err){
    statusEl.innerHTML = '<span class="err">'+err.message+'</span>';
  }finally{
    document.getElementById('run').disabled = false;
  }
});
</script>
</div></body></html>
"""


def _render_page() -> str:
    rows = []
    for label, name, hint in ROLE_FIELDS:
        rows.append(
            f'<div class="role"><div class="meta"><div class="name">{name}</div>'
            f'<div class="hint">{hint}</div></div>'
            f'<input type="file" accept="audio/*,.wav,.m4a,.mp3,.flac,.ogg,.webm" '
            f'data-role="{label}"><span class="picked"></span></div>'
        )
    return PAGE.replace("__ROLE_ROWS__", "\n".join(rows))


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _render_page()


@app.post("/run")
async def run(
    single_phone: UploadFile | None = File(None),
    raw_phone_2: UploadFile | None = File(None),
    natural: UploadFile | None = File(None),
    studio_voice: UploadFile | None = File(None),
    karaoke: UploadFile | None = File(None),
    party: UploadFile | None = File(None),
    mono_downmix: UploadFile | None = File(None),
    gen_enhanced: str = Form("true"),
    gen_mono: str = Form("true"),
    target_lufs: float = Form(-16.0),
) -> JSONResponse:
    uploads: dict[str, UploadFile | None] = {
        "single_phone": single_phone,
        "raw_phone_2": raw_phone_2,
        "natural": natural,
        "studio_voice": studio_voice,
        "karaoke": karaoke,
        "party": party,
        "mono_downmix": mono_downmix,
    }
    attached = {k: v for k, v in uploads.items() if v is not None}
    if not attached:
        return JSONResponse({"detail": "Attach at least one file."}, status_code=400)

    run_dir = Path(tempfile.mkdtemp(prefix="bench_gui_"))
    in_dir = run_dir / "in"
    in_dir.mkdir(parents=True, exist_ok=True)

    # Save uploads to disk (preserving extension so FFmpeg can sniff the codec).
    saved: dict[str, str] = {}
    for label, up in attached.items():
        suffix = Path(up.filename or "").suffix or ".wav"
        dest = in_dir / f"{label}{suffix}"
        with open(dest, "wb") as fh:
            fh.write(await up.read())
        saved[label] = str(dest)

    want_enh = str(gen_enhanced).lower() in ("true", "1", "on", "yes")
    want_mono = str(gen_mono).lower() in ("true", "1", "on", "yes")

    try:
        clips = _build_clips_from_uploads(saved, want_enh, want_mono)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"detail": f"Could not load files: {exc}"}, status_code=400)
    if not clips:
        return JSONResponse({"detail": "No usable audio could be loaded."}, status_code=400)

    out_dir = run_dir / "out"
    try:
        result = bench.build_report(
            clips, out_dir, target_lufs=float(target_lufs), title="Audio Test Bench"
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"detail": f"Report failed: {exc}"}, status_code=500)

    token = run_dir.name
    _RUNS[token] = out_dir
    return JSONResponse(
        {
            "token": token,
            "clips": result["clips"],
            "exports": result["exports"],
            "checks_total": result["checks_total"],
            "checks_failed": result["checks_failed"],
        }
    )


def _build_clips_from_uploads(
    saved: dict[str, str], want_enhanced: bool, want_mono: bool
) -> list[bench.Clip]:
    """Turn the saved uploads into clips in a stable, report-friendly order.

    - Each attached file is tagged with the role implied by its field name.
    - studio_voice/karaoke/party and mono_downmix are auto-generated from the
      natural mix ONLY when the user did not attach them and asked us to.
    """
    order = [
        "single_phone",
        "raw_phone_2",
        "natural",
        "studio_voice",
        "karaoke",
        "party",
        "mono_downmix",
    ]
    clips: list[bench.Clip] = []
    natural_clip: bench.Clip | None = None

    for label in order:
        path = saved.get(label)
        if not path:
            continue
        clip = bench.load_clip_as(label, path)
        clips.append(clip)
        if label == "natural":
            natural_clip = clip

    have = {c.label for c in clips}

    # Auto-generate enhanced presets from the natural mix where not provided.
    if natural_clip is not None and want_enhanced:
        for mode in ("studio_voice", "karaoke", "party"):
            if mode in have:
                continue
            enhanced = bench.effects.apply_enhancement(
                natural_clip.stereo, natural_clip.sr, mode
            )
            clips.append(
                bench.Clip(mode, enhanced, natural_clip.sr, "enhanced", "(generated)")
            )

    # Auto-generate mono down-mix from the natural mix if not provided.
    if natural_clip is not None and want_mono and "mono_downmix" not in have:
        import numpy as np

        mono = natural_clip.mono
        mono_st = np.column_stack([mono, mono]).astype(np.float32)
        clips.append(
            bench.Clip("mono_downmix", mono_st, natural_clip.sr, "mono_downmix")
        )

    # Keep a clean, predictable order for the report.
    rank = {lbl: i for i, lbl in enumerate(order)}
    clips.sort(key=lambda c: rank.get(c.label, 99))
    return clips


def _safe_run_path(token: str, name: str) -> Path | None:
    out_dir = _RUNS.get(token)
    if out_dir is None:
        return None
    target = (out_dir / name).resolve()
    if not str(target).startswith(str(out_dir.resolve())):
        return None  # path traversal guard
    return target if target.exists() else None


@app.get("/report/{token}", response_class=HTMLResponse)
def report(token: str) -> HTMLResponse:
    path = _safe_run_path(token, "report.html")
    if path is None:
        return HTMLResponse("<h3>Report not found.</h3>", status_code=404)
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/file/{token}/{name}")
def file(token: str, name: str):
    from fastapi.responses import FileResponse

    path = _safe_run_path(token, name)
    if path is None:
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(str(path), filename=name)


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    host, port = "127.0.0.1", 8200
    url = f"http://{host}:{port}"
    print(f"Audio Test Bench GUI -> {url}")
    threading.Timer(1.0, _open_browser, args=(url,)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
