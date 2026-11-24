#!/usr/bin/env python3
# Copyright 2026 aryand1 and PaperOrchestra skill-pack contributors.
# Licensed under the Apache License, Version 2.0 (see po_official/LICENSE-APACHE-2.0).
"""PaperOrchestra official mode: run Google's pipeline with the host agent as the model.

This drives the port of google-research/paper-orchestra (po_official/). The
pipeline code is Google's; every place it would call Gemini, it instead writes
the exact model input to a prompt file and stops. The host agent answers each
pending call (ideally in a fresh sub-agent), then runs `advance` again. The
pipeline replays from the start using the saved answers, so the control flow
(stage order, retries, accept/revert rules) is decided by Google's code, not by
the host agent.

Usage:
  python skills/paper-orchestra/scripts/po.py advance --workspace workspace/ [options]
  python skills/paper-orchestra/scripts/po.py status  --workspace workspace/
  python skills/paper-orchestra/scripts/po.py reset   --workspace workspace/ [--all]

`advance` prints one JSON object to stdout:
  {"status": "pending", "calls": [{id, prompt_file, response_file, tools, attachments}, ...]}
  {"status": "done", "final_pdf": ..., "final_tex": ...}
  {"status": "failed" | "error", "message": ...}
Pipeline logs go to <workspace>/.po/run.log.
"""

import argparse
import contextlib
import datetime
import io
import json
import os
import shutil
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

RAW_EXCLUDE = {"conference_guidelines.md", "guidelines.md", "template.tex"}
TEMPLATE_EXTS = (".tex", ".sty", ".cls", ".bst", ".bib", ".bbx", ".cbx", ".def", ".cfg", ".clo")

DEFAULTS = {
    "use_plotting": False,               # official CLI default
    "research_cutoff": None,              # official: current Year-Month
    "idea_filename": "idea.md",           # official CLI default: idea_sparse.md
    "experimental_log_filename": "experimental_log.md",
    "latex_template_dir": None,           # official: --latex_template_dir (required)
    "writer_model_name": "gemini-3.1-pro-preview",
    "reflection_model_name": "gemini-3.1-pro-preview",
    "plotting_model_name": "gemini-3.1-pro-preview",
    "image_model_name": "gemini-3-pro-image-preview",
    "plotting_max_critic_rounds": 3,
}


def _po_dir(ws):
    return os.path.join(ws, ".po")


def _load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def _save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _bool(x):
    return str(x).lower() in ["true", "1", "yes"]


def load_config(ws, args):
    po = _po_dir(ws)
    os.makedirs(po, exist_ok=True)
    path = os.path.join(po, "config.json")
    cfg = _load_json(path, None)
    given = {
        "use_plotting": None if args.use_plotting is None else _bool(args.use_plotting),
        "research_cutoff": args.research_cutoff,
        "idea_filename": args.idea_filename,
        "experimental_log_filename": args.experimental_log_filename,
        "latex_template_dir": os.path.abspath(args.latex_template_dir) if args.latex_template_dir else None,
        "plotting_max_critic_rounds": args.plotting_max_critic_rounds,
    }
    if cfg is None:
        cfg = dict(DEFAULTS)
        for k, v in given.items():
            if v is not None:
                cfg[k] = v
        if not cfg["research_cutoff"]:
            cfg["research_cutoff"] = datetime.datetime.now().strftime("%Y-%m")
        cfg["source_date_epoch"] = str(int(time.time()))
        cfg["created"] = datetime.datetime.now().isoformat(timespec="seconds")
        _save_json(path, cfg)
    else:
        changed = {k: v for k, v in given.items() if v is not None and cfg.get(k) != v}
        if changed:
            raise SystemExit(json.dumps({
                "status": "error",
                "message": f"This workspace was started with different settings {sorted(changed)}. "
                           "Run `po.py reset --workspace ... --all` to start over with new settings.",
            }))
    return cfg


def prepare_workspace(ws, cfg):
    """Lay out the workspace the way the official CLI does (base_dir/raw_materials + template dir)."""
    inputs = os.path.join(ws, "inputs")
    if not os.path.isdir(inputs):
        raise RuntimeError(f"Missing {inputs}/ (put idea.md, experimental_log.md, template.tex, "
                           "conference_guidelines.md and optional figures/ there)")
    # base_dir/raw_materials: the official CLI copies --raw_materials_dir here.
    raw = os.path.join(ws, "raw_materials")
    if os.path.exists(raw):
        shutil.rmtree(raw)
    os.makedirs(raw)
    for name in sorted(os.listdir(inputs)):
        src = os.path.join(inputs, name)
        if name == "figures" and os.path.isdir(src):
            shutil.copytree(src, os.path.join(raw, "figures"))
        elif os.path.isfile(src) and name not in RAW_EXCLUDE and not name.endswith(TEMPLATE_EXTS):
            shutil.copy(src, os.path.join(raw, name))

    # latex template dir: either given (e.g. Google's templates/cvpr2025) or built from inputs/.
    if cfg.get("latex_template_dir"):
        tdir = cfg["latex_template_dir"]
    else:
        tdir = os.path.join(_po_dir(ws), "template")
        if os.path.exists(tdir):
            shutil.rmtree(tdir)
        os.makedirs(tdir)
        for name in sorted(os.listdir(inputs)):
            src = os.path.join(inputs, name)
            if not os.path.isfile(src):
                continue
            if name == "conference_guidelines.md":
                shutil.copy(src, os.path.join(tdir, "guidelines.md"))
            elif name == "guidelines.md" or name.endswith(TEMPLATE_EXTS):
                shutil.copy(src, os.path.join(tdir, name))
    return raw, tdir


def configure_runtime(ws, cfg):
    po = _po_dir(ws)
    from po_official import llm, scholar_utils, pdf_utils, paper_banana_utils

    os.environ["SOURCE_DATE_EPOCH"] = cfg["source_date_epoch"]  # reproducible PDF bytes
    llm.configure(os.path.join(po, "calls"), attempt=1)
    scholar_utils.configure_s2_cache(os.path.join(po, "s2_cache.json"))
    pdf_utils.configure_memo(os.path.join(po, "compile_cache"))
    paper_banana_utils.PLOT_CACHE_DIR["path"] = os.path.join(po, "plot_cache")


def finalize(ws):
    final_dir = os.path.join(ws, "final")
    os.makedirs(final_dir, exist_ok=True)
    pdf = os.path.join(ws, "final_paper.pdf")
    tex = os.path.join(ws, "content_refinement_workdir", "final_refined_paper.tex")
    out = {"status": "done"}
    if os.path.exists(pdf):
        shutil.copy(pdf, os.path.join(final_dir, "paper.pdf"))
        out["final_pdf"] = os.path.join(final_dir, "paper.pdf")
    if os.path.exists(tex):
        shutil.copy(tex, os.path.join(final_dir, "paper.tex"))
        out["final_tex"] = os.path.join(final_dir, "paper.tex")
    bib = os.path.join(ws, "content_refinement_workdir", "references.bib")
    if os.path.exists(bib):
        shutil.copy(bib, os.path.join(final_dir, "references.bib"))
    wl = os.path.join(ws, "content_refinement_workdir", "content_refinement_worklog.json")
    if os.path.exists(wl):
        out["refinement_worklog"] = wl
    return out


def cmd_advance(args):
    ws = os.path.abspath(args.workspace)
    cfg = load_config(ws, args)
    po = _po_dir(ws)
    state_path = os.path.join(po, "state.json")
    state = _load_json(state_path, {"failed_tries": 0, "failures": []})
    if state.get("done"):
        return finalize(ws)

    log_path = os.path.join(po, "run.log")
    log = open(log_path, "a", encoding="utf-8")
    log.write(f"\n===== advance {datetime.datetime.now().isoformat(timespec='seconds')} =====\n")

    def on_try_failed(try_number, tb):
        state["failed_tries"] = try_number
        state["failures"].append({"try": try_number, "traceback": tb[-4000:]})
        _save_json(state_path, state)

    from po_official import llm
    result = None
    try:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            raw, tdir = prepare_workspace(ws, cfg)
            configure_runtime(ws, cfg)
            common = dict(
                base_dir=ws,
                latex_template_dir=tdir,
                idea_filename=cfg["idea_filename"],
                experimental_log_filename=cfg["experimental_log_filename"],
                writer_model_name=cfg["writer_model_name"],
                reflection_model_name=cfg["reflection_model_name"],
                research_cutoff=cfg["research_cutoff"],
                start_try=state.get("failed_tries", 0),
                on_try_failed=on_try_failed,
            )
            if cfg["use_plotting"]:
                from po_official.paper_writer_with_plotting import write_single_paper
                ok = write_single_paper(
                    plotting_model_name=cfg["plotting_model_name"],
                    image_model_name=cfg["image_model_name"],
                    plotting_max_critic_rounds=cfg["plotting_max_critic_rounds"],
                    **common,
                )
            else:
                figs = os.path.join(raw, "figures")
                if not (os.path.isdir(figs) and os.path.exists(os.path.join(figs, "info.json"))):
                    print("WARNING: plotting is off and inputs/figures/info.json is missing, so the "
                          "section writer will see no figures (same as the official code).")
                from po_official.paper_writer import write_single_paper
                ok = write_single_paper(**common)
        if ok:
            state["done"] = True
            _save_json(state_path, state)
            result = finalize(ws)
        else:
            result = {"status": "failed",
                      "message": "All 3 tries failed (same limit as the official code). See .po/run.log "
                                 "and .po/state.json.",
                      "failures": [f["traceback"].strip().splitlines()[-1] for f in state["failures"]]}
    except llm.Pending as p:
        calls = p.calls
        _save_json(os.path.join(po, "pending.json"), calls)
        result = {
            "status": "pending",
            "try": state.get("failed_tries", 0) + 1,
            "count": len(calls),
            "calls": calls,
            "next": "Answer every call (see skills/shared/official-call-protocol.md), then run advance again.",
        }
    except llm.StaleCallError as e:
        result = {"status": "error", "message": str(e)}
    except SystemExit:
        raise
    except Exception as e:  # setup errors outside the official try-loop
        log.write(traceback.format_exc())
        result = {"status": "error", "message": f"{type(e).__name__}: {e}"}
    finally:
        log.close()
    return result


def cmd_status(args):
    ws = os.path.abspath(args.workspace)
    po = _po_dir(ws)
    cfg = _load_json(os.path.join(po, "config.json"), None)
    state = _load_json(os.path.join(po, "state.json"), {})
    pending = _load_json(os.path.join(po, "pending.json"), [])
    calls_dir = os.path.join(po, "calls")
    answered = 0
    for root, _d, files in os.walk(calls_dir):
        answered += sum(1 for f in files if f.endswith(".response.md"))
    return {"config": cfg, "state": state, "answered_calls": answered,
            "pending_calls_at_last_advance": len(pending)}


def cmd_reset(args):
    ws = os.path.abspath(args.workspace)
    po = _po_dir(ws)
    targets = ["raw_materials", "latex_writeup", "literature_agent_output", "content_refinement_workdir",
               "figures", "outline.json", "plotting_results.json", "final_paper.pdf", "time_breakdown.json"]
    for t in targets:
        p = os.path.join(ws, t)
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.exists(p):
            os.remove(p)
    if args.all:
        if os.path.exists(po):
            shutil.rmtree(po)
    else:
        for name in ["state.json", "pending.json"]:
            p = os.path.join(po, name)
            if os.path.exists(p):
                os.remove(p)
        if os.path.exists(os.path.join(po, "calls")):
            shutil.rmtree(os.path.join(po, "calls"))
    return {"status": "reset", "all": bool(args.all)}


def _pin_hash_seed():
    """Re-run under PYTHONHASHSEED=0 so replays are identical.

    The official code builds some lists with list(set(...)) (e.g. the
    Introduction's citation_candidates), whose order changes with Python's
    per-process hash seed. Each `advance` replays the pipeline in a new
    process, so the seed must be fixed for the prompts to stay identical.
    Any fixed seed yields one of the orders the official code could produce.
    """
    if sys.flags.hash_randomization and os.environ.get("PO_HASHSEED_PINNED") != "1":
        env = dict(os.environ, PYTHONHASHSEED="0", PO_HASHSEED_PINNED="1")
        os.execve(sys.executable, [sys.executable, os.path.abspath(__file__)] + sys.argv[1:], env)


def main():
    _pin_hash_seed()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("advance")
    a.add_argument("--workspace", required=True)
    a.add_argument("--use_plotting", "--use-plotting", dest="use_plotting", default=None,
                   help="Enable the plotting agent (official default: false).")
    a.add_argument("--research_cutoff", "--research-cutoff", dest="research_cutoff", default=None,
                   help="Year-Month cutoff, e.g. 2024-11 (official default: current Year-Month).")
    a.add_argument("--idea_filename", "--idea-filename", dest="idea_filename", default=None)
    a.add_argument("--experimental_log_filename", dest="experimental_log_filename", default=None)
    a.add_argument("--latex_template_dir", "--latex-template-dir", dest="latex_template_dir", default=None,
                   help="Official-style template dir (template.tex + guidelines.md + style files).")
    a.add_argument("--plotting_max_critic_rounds", type=int, default=None)
    s = sub.add_parser("status")
    s.add_argument("--workspace", required=True)
    r = sub.add_parser("reset")
    r.add_argument("--workspace", required=True)
    r.add_argument("--all", action="store_true", help="Also delete settings, caches and saved answers.")
    args = ap.parse_args()
    out = {"advance": cmd_advance, "status": cmd_status, "reset": cmd_reset}[args.cmd](args)
    print(json.dumps(out, indent=2))
    return 0 if out.get("status") not in ("error", "failed") else 1


if __name__ == "__main__":
    sys.exit(main())
