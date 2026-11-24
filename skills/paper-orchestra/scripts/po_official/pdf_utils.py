# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# MODIFIED (2026, PaperOrchestra skill pack), from utils/pdf_utils.py at
# google-research/paper-orchestra@d9bae99. Kept: load_paper, compile_latex,
# pdf_to_grid_images (the three functions the pipeline uses). Changes:
# PyMuPDF is optional (load_paper already fell back to pypdf; grids fall back
# to poppler's pdftoppm); grids are composed with Pillow instead of OpenCV
# (same pixels); `rich` output is plain text; compile_latex and the grids are
# memoized by input hash so a resumed run does not rebuild finished PDFs.

import os
import re
import json
import shutil
import hashlib
import subprocess
import tempfile

from pypdf import PdfReader  # type: ignore

_MEMO = {"cache_dir": None}


def configure_memo(cache_dir: str):
    _MEMO["cache_dir"] = cache_dir
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)


def _sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def _compile_inputs_hash(cwd, texfile_name):
    h = hashlib.sha256(texfile_name.encode())
    for name in sorted(os.listdir(cwd)):
        p = os.path.join(cwd, name)
        if os.path.isfile(p) and name.endswith((".tex", ".bib", ".sty", ".cls", ".bst")):
            h.update(name.encode() + b"\x00" + _sha_file(p).encode())
    return h.hexdigest()


def _compile_memo_check(cwd, pdf_file, texfile_name):
    """Restore a cached build of identical inputs instead of recompiling."""
    cache_dir = _MEMO["cache_dir"]
    if not cache_dir:
        return None, False
    digest = _compile_inputs_hash(cwd, texfile_name)
    cached_pdf = os.path.join(cache_dir, f"{digest}.pdf")
    if os.path.exists(cached_pdf):
        shutil.copy(cached_pdf, pdf_file)
        return digest, True
    if os.path.exists(os.path.join(cache_dir, f"{digest}.fail")):
        if os.path.exists(pdf_file):
            os.remove(pdf_file)
        return digest, True
    return digest, False


def _compile_memo_store(digest, pdf_file):
    cache_dir = _MEMO["cache_dir"]
    if not cache_dir or not digest:
        return
    if os.path.exists(pdf_file):
        shutil.copy(pdf_file, os.path.join(cache_dir, f"{digest}.pdf"))
    else:
        open(os.path.join(cache_dir, f"{digest}.fail"), "w").close()


def _plain_print(msg):
    print(re.sub(r"\[/?[a-z ]+\]", "", str(msg)))


def load_paper(pdf_path, num_pages=None, min_size=100):
    try:
        import pymupdf  # type: ignore  # optional; pypdf fallback below
        doc = pymupdf.open(pdf_path)
        if num_pages:
            doc = doc[:num_pages]
        text = ""
        for page in doc:
            text += page.get_text()
        if len(text) < min_size:
            raise Exception("Text too short")
    except Exception as e:
        print(f"Error with pymupdf, falling back to pypdf: {e}")
        reader = PdfReader(pdf_path)
        if num_pages is None:
            pages = reader.pages
        else:
            pages = reader.pages[:num_pages]
        text = "".join(page.extract_text() for page in pages)
        if len(text) < min_size:
            raise Exception("Text too short")
    return text




def compile_latex(cwd, pdf_file, texfile_name="template", timeout=30):
    import os, shutil, subprocess, traceback

    memo_key, memo_hit = _compile_memo_check(cwd, pdf_file, texfile_name)
    if memo_hit:
        return

    source_pdf = os.path.join(cwd, f"{texfile_name}.pdf")
    if os.path.exists(source_pdf):
        os.remove(source_pdf)

    env = dict(os.environ)
    for var in ["TEXINPUTS", "TEXMFHOME", "TEXMF", "TEXMFVAR", "TEXMFCONFIG"]:
        env.pop(var, None)

    commands = [
        ["pdflatex", "-interaction=nonstopmode", f"{texfile_name}.tex"],
        ["bibtex", texfile_name],
        ["pdflatex", "-interaction=nonstopmode", f"{texfile_name}.tex"],
        ["pdflatex", "-interaction=nonstopmode", f"{texfile_name}.tex"],
    ]

    execution_logs = []

    for command in commands:
        cmd_str = " ".join(command)
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,  # Captures stdout/stderr silently
                text=True,
                timeout=timeout,
                env=env,
            )

            execution_logs.append(
                {
                    "cmd": cmd_str,
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            )

        except subprocess.TimeoutExpired:
            execution_logs.append(
                {"cmd": cmd_str, "error": f"TIMEOUT after {timeout}s"}
            )
        except Exception as e:
            execution_logs.append({"cmd": cmd_str, "error": str(e)})

    has_timeout = any(
        "error" in log and "TIMEOUT" in log["error"] for log in execution_logs
    )

    if os.path.exists(source_pdf) and not has_timeout:
        try:
            shutil.move(source_pdf, pdf_file)
        except Exception:
            _plain_print(f"[bold red]Error moving generated PDF to {pdf_file}[/bold red]")
            print(traceback.format_exc())
    else:
        _plain_print(
            f"\n[bold red]❌ PDF Generation Failed (No PDF file produced)[/bold red]"
        )

        for log in execution_logs:
            if "error" in log:
                header_color = "red"
            elif log["returncode"] == 0:
                header_color = "green"
            else:
                header_color = "yellow"  # Warning/Error

            _plain_print(
                f"\n[bold {header_color}]---> Ran: {log['cmd']}[/bold {header_color}]"
            )

            if "error" in log:
                _plain_print(f"[bold red]SYSTEM ERROR: {log['error']}[/bold red]")
            else:
                if log["stdout"].strip():
                    _plain_print(f"[dim]{log['stdout'].strip()}[/dim]")
                if log["stderr"].strip():
                    _plain_print(f"[red]{log['stderr'].strip()}[/red]")

    _compile_memo_store(memo_key, pdf_file)


def pdf_to_grid_images(pdf_path, output_dir, scale=2.0):
    """Converts a PDF to 2x2 grid images of its pages.
    Returns a list of paths to the generated grid images.
    """
    from PIL import Image  # type: ignore

    os.makedirs(output_dir, exist_ok=True)
    stamp = os.path.join(output_dir, ".source_sha256")
    src_sha = _sha_file(pdf_path)
    if os.path.exists(stamp):
        with open(stamp, "r", encoding="utf-8") as f:
            cached = f.read().split()
        if cached and cached[0] == src_sha:
            paths = [os.path.join(output_dir, n) for n in cached[1:]]
            if all(os.path.exists(p) for p in paths):
                return paths

    pages = []
    try:
        import pymupdf  # type: ignore

        doc = pymupdf.open(pdf_path)
        mat = pymupdf.Matrix(scale, scale)
        for page in doc:
            pix = page.get_pixmap(matrix=mat)
            mode = "RGBA" if pix.n == 4 else "RGB"
            pages.append(Image.frombytes(mode, (pix.w, pix.h), pix.samples).convert("RGB"))
    except ImportError:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                ["pdftoppm", "-r", str(int(72 * scale)), "-png", pdf_path, os.path.join(tmp, "p")],
                check=True, capture_output=True,
            )
            for name in sorted(os.listdir(tmp), key=lambda n: int(re.findall(r"(\d+)", n)[-1])):
                pages.append(Image.open(os.path.join(tmp, name)).convert("RGB"))

    grid_image_paths = []
    for i in range(0, len(pages), 4):
        chunk = pages[i : i + 4]
        while len(chunk) < 4:
            chunk.append(Image.new("RGB", chunk[0].size, (255, 255, 255)))
        w0, h0 = chunk[0].size
        w1, h1 = chunk[1].size
        grid = Image.new("RGB", (w0 + w1, max(h0, h1) + max(chunk[2].size[1], chunk[3].size[1])), (255, 255, 255))
        grid.paste(chunk[0], (0, 0))
        grid.paste(chunk[1], (w0, 0))
        grid.paste(chunk[2], (0, max(h0, h1)))
        grid.paste(chunk[3], (chunk[2].size[0], max(h0, h1)))
        grid_path = os.path.join(output_dir, f"grid_{i//4 + 1}.png")
        grid.save(grid_path)
        grid_image_paths.append(grid_path)

    with open(stamp, "w", encoding="utf-8") as f:
        f.write(" ".join([src_sha] + [os.path.basename(p) for p in grid_image_paths]))
    return grid_image_paths
