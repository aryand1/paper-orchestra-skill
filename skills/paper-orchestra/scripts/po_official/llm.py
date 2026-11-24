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
# MODIFIED (2026, PaperOrchestra skill pack): replaces utils/gemini_utils.py
# from google-research/paper-orchestra@d9bae99. The three response parsers
# below are copied verbatim. The call functions keep the official signatures,
# but instead of calling Gemini they write the exact model input to a prompt
# file and wait for the host agent (Claude Code, Cursor, ...) to write the
# reply. See skills/shared/official-call-protocol.md.

import re
import json
import os
import hashlib
import contextlib
from typing import Dict, Any

from po_official.genai_shim import Part

def parse_gemini_json_results(response: str):
    if not response or not isinstance(response, str):
        return None

    json_pattern = r"```json(.*?)```"
    matches = re.findall(json_pattern, response, re.DOTALL)

    if not matches:
        json_pattern = r"(\{.*\}|\[.*\])"
        matches = re.findall(json_pattern, response, re.DOTALL)

    for json_string in matches:
        json_string = json_string.strip()
        try:
            parsed_json = json.loads(json_string)
            return parsed_json
        except json.JSONDecodeError:
            try:
                json_string_clean = re.sub(r"[\x00-\x1F\x7F]", "", json_string)
                json_string_clean = re.sub(r",\s*([\]}])", r"\1", json_string_clean)
                json_string_clean = re.sub(
                    r'\\(?![\\"/bfnrtu])', r"\\\\", json_string_clean
                )

                parsed_json = json.loads(json_string_clean)
                return parsed_json
            except json.JSONDecodeError:
                continue

    try:
        start_idx = response.find("{")
        end_idx = response.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_string = response[start_idx : end_idx + 1]
            json_string_clean = re.sub(r"[\x00-\x1F\x7F]", "", json_string)
            json_string_clean = re.sub(r",\s*([\]}])", r"\1", json_string_clean)
            json_string_clean = re.sub(
                r'\\(?![\\"/bfnrtu])', r"\\\\", json_string_clean
            )
            return json.loads(json_string_clean)
    except Exception:
        pass

    return None  # No valid JSON found


def parse_gemini_latex_results(response: str):
    match = re.search(r"```latex\s*(.*?)\s*```", response.strip(), re.DOTALL)
    if match:
        latex_string = match.group(1)
    else:
        latex_string = response
    return latex_string


def parse_gemini_json_latex_response(response_text: str) -> Dict[str, Any]:
    """
    Extracts the JSON worklog and the LaTeX code from the LLM response.
    Expected format:
      ... arbitrary text ...
      ```json
      { ... }
      ```
      ... arbitrary text ...
      ```latex
      ...
      ```
    """
    result = {"json": None, "latex": None}

    json_match = re.search(r"```json\s*(.*?)\s*```", response_text, re.DOTALL)
    if json_match:
        try:
            result["json"] = json.loads(json_match.group(1))
        except json.JSONDecodeError:
            print(" >> Error: Failed to parse JSON worklog from response.")
            result["json"] = {"error": "Invalid JSON format returned"}

    latex_match = re.search(r"```latex\s*(.*?)\s*```", response_text, re.DOTALL)
    if latex_match:
        result["latex"] = latex_match.group(1)
    else:
        print(" >> Warning: No ```latex``` block found.")

    return json.dumps(result)



def identity_parse(response: str):
    return response


# ---------------------------------------------------------------------------
# Host-agent call layer (replaces the Gemini client)
# ---------------------------------------------------------------------------

OFFICIAL_DEFAULT_MAX_RETRIES = 5

_CTX = {"calls_dir": None, "attempt": 1, "stack": [], "counters": {}}


class Pending(BaseException):
    """Raised when one or more LLM calls are waiting for a host reply.

    Subclasses BaseException on purpose: the official code wraps many steps in
    `except Exception`, and a pending call must pass through those handlers
    untouched instead of being treated as a failure.
    """

    def __init__(self, calls):
        super().__init__(f"{len(calls)} pending call(s)")
        self.calls = list(calls)


class StaleCallError(BaseException):
    """Inputs of an already-answered call changed. BaseException so the
    official try-loop does not count it as a failed attempt."""


def configure(calls_dir: str, attempt: int = 1):
    _CTX["calls_dir"] = calls_dir
    _CTX["attempt"] = attempt
    _CTX["stack"] = []
    _CTX["counters"] = {}


@contextlib.contextmanager
def scope(name: str):
    """Names a branch of the pipeline so call ids stay stable on replay."""
    _CTX["stack"].append(str(name).replace("/", "_").replace(" ", "_"))
    try:
        yield
    finally:
        _CTX["stack"].pop()


def scoped(name: str):
    """Decorator form of scope()."""
    def deco(fn):
        def wrapper(*args, **kwargs):
            with scope(name):
                return fn(*args, **kwargs)
        wrapper.__name__ = getattr(fn, "__name__", "scoped")
        return wrapper
    return deco


def gather(*fns):
    """Run independent branches; collect every pending call before stopping.

    The official code runs these branches in threads. Here they run one after
    another, and if any branch is waiting on the host, all waiting calls from
    all branches are reported together so the host can answer them in parallel.
    """
    results, pending = [], []
    for fn in fns:
        try:
            results.append(fn())
        except Pending as p:
            pending.extend(p.calls)
            results.append(None)
    if pending:
        raise Pending(pending)
    return results


def _next_call_id() -> str:
    path = "/".join(_CTX["stack"]) or "root"
    n = _CTX["counters"].get(path, 0) + 1
    _CTX["counters"][path] = n
    return f"a{_CTX['attempt']}/{path}/c{n:02d}"


def _ext_for(data: bytes, mime_type: str) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"%PDF":
        return ".pdf"
    return {"image/png": ".png", "image/jpeg": ".jpg", "application/pdf": ".pdf"}.get(
        mime_type, ".bin"
    )


def _as_parts(contents):
    if not isinstance(contents, (list, tuple)):
        contents = [contents]
    parts = []
    for c in contents:
        if isinstance(c, Part):
            parts.append(c)
        else:
            parts.append(Part(text=str(c)))
    return parts


def _describe_settings(generation_configs: dict):
    cfg = dict(generation_configs or {})
    tools = []
    for t in cfg.pop("tools", []) or []:
        if getattr(t, "google_search", None) is not None:
            tools.append("web_search")
    system = cfg.pop("system_instruction", None)
    return system, tools, cfg


def _render(call_id, retry, contents, generation_configs, model_name, max_retries):
    calls_dir = _CTX["calls_dir"]
    if not calls_dir:
        raise RuntimeError("llm.configure() was not called")
    base = os.path.join(calls_dir, call_id)
    os.makedirs(os.path.dirname(base), exist_ok=True)
    prompt_path = f"{base}.r{retry}.prompt.md"
    response_path = f"{base}.r{retry}.response.md"

    system, tools, settings = _describe_settings(generation_configs)
    parts = _as_parts(contents)

    body, attachments, hasher = [], [], hashlib.sha256()
    hasher.update(f"model={model_name}|settings={sorted(settings.items())!r}|tools={tools}".encode())
    if system is not None:
        body.append("=" * 24 + " SYSTEM INSTRUCTION " + "=" * 24)
        body.append(str(system))
        hasher.update(b"SYS\x00" + str(system).encode())
    for i, p in enumerate(parts, 1):
        if p.data is not None:
            digest = hashlib.sha256(p.data).hexdigest()
            att_path = f"{base}.att{i}{_ext_for(p.data, p.mime_type or '')}"
            if not os.path.exists(att_path):
                with open(att_path, "wb") as fh:
                    fh.write(p.data)
            attachments.append(att_path)
            kind = "pdf" if att_path.endswith(".pdf") else "image"
            body.append("=" * 24 + f" USER CONTENT · PART {i} (attachment: {kind}) " + "=" * 24)
            body.append(f"[Attachment: {att_path} — open it with your Read tool]")
            hasher.update(b"ATT\x00" + digest.encode())
        else:
            body.append("=" * 24 + f" USER CONTENT · PART {i} (text) " + "=" * 24)
            body.append(p.text or "")
            hasher.update(b"TXT\x00" + (p.text or "").encode())
    body.append("=" * 24 + " END OF MODEL INPUT " + "=" * 24)
    prompt_hash = hasher.hexdigest()

    setting_str = ", ".join(f"{k}={v}" for k, v in sorted(settings.items())) or "defaults"
    header = [
        "<!-- PaperOrchestra official-mode LLM call -->",
        f"<!-- prompt_sha256: {prompt_hash} -->",
        f"# LLM call `{call_id}` (try {retry} of {max_retries})",
        "",
        "**Executor instructions (not part of the model input).** Act as the model for this one call.",
        "Use the SYSTEM INSTRUCTION (if any) as your system prompt and the USER CONTENT parts, in order,",
        "as the user turn. Open every attachment with your Read tool at the position it appears.",
        "Use no other context: no repo files, no earlier conversation, no memory.",
        ("You MAY use web search for this call (the official call used Gemini Google Search grounding)."
         if tools else "Do NOT use web search or any other tool except Read for attachments."),
        f"Write your complete reply, exactly as the model would return it, to `{response_path}`.",
        "",
        f"- Official model: `{model_name}`",
        f"- Official generation settings: {setting_str} (this host cannot set them)",
        f"- Tools: {', '.join(tools) if tools else 'none'}",
    ]
    if retry > 1:
        header.append(
            f"- This is a retry: the reply in try {retry - 1} could not be parsed by the official "
            "parser. Follow the output format the prompt asks for exactly."
        )
    text = "\n".join(header) + "\n\n" + "\n".join(body) + "\n"

    existing_hash = None
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as fh:
            m = re.search(r"prompt_sha256: ([0-9a-f]{64})", fh.read(400))
            existing_hash = m.group(1) if m else None
    if existing_hash != prompt_hash:
        if existing_hash is not None and _has_response(response_path):
            with open(prompt_path.replace(".prompt.md", ".prompt.changed.md"), "w", encoding="utf-8") as fh:
                fh.write(text)
            raise StaleCallError(
                f"Inputs changed for answered call {call_id}. Run `po.py reset` and start again."
            )
        with open(prompt_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    info = {
        "id": f"{call_id}.r{retry}",
        "prompt_file": prompt_path,
        "response_file": response_path,
        "tools": tools,
        "attachments": attachments,
        "official_model": model_name,
    }
    return info


def _has_response(path):
    return os.path.exists(path) and os.path.getsize(path) > 0


def _invoke(contents, model_name, generation_configs, result_parsing_func,
            check_parsed_response_not_none, max_retries):
    call_id = _next_call_id()
    for retry in range(1, max_retries + 1):
        info = _render(call_id, retry, contents, generation_configs, model_name, max_retries)
        if not _has_response(info["response_file"]):
            raise Pending([info])
        with open(info["response_file"], "r", encoding="utf-8") as fh:
            raw_response = fh.read()
        parsed_response = result_parsing_func(raw_response)
        if check_parsed_response_not_none and parsed_response is None:
            print(f"Warning: reply for {info['id']} could not be parsed; retrying.")
            continue
        return {"raw_response": raw_response, "parsed_response": parsed_response}
    raise RuntimeError(f"Error: All {max_retries} retry attempts failed for {call_id}.")


def call_gemini_with_contents(
    contents,
    model_name: str,
    max_retries: int = 5,
    base_interval_sec: int = 5,
    result_parsing_func: callable = parse_gemini_json_results,
    generation_configs: dict = {},
    check_parsed_response_not_none: bool = True,
):
    return _invoke(contents, model_name, generation_configs, result_parsing_func,
                   check_parsed_response_not_none, max_retries)


def call_gemini_with_text_prompt(
    prompt: str,
    model_name: str,
    result_parsing_func: callable = parse_gemini_json_results,
    generation_configs: dict = {},
    check_parsed_response_not_none: bool = True,
    max_retries: int = 5,
    base_interval_sec: int = 5,
):
    return call_gemini_with_contents(
        contents=[Part(text=prompt)],
        model_name=model_name,
        result_parsing_func=result_parsing_func,
        generation_configs=generation_configs,
        check_parsed_response_not_none=check_parsed_response_not_none,
        max_retries=max_retries,
        base_interval_sec=base_interval_sec,
    )


def call_gemini_with_images(
    prompt: str,
    images: list,
    model_name: str,
    result_parsing_func: callable = parse_gemini_json_results,
    generation_configs: dict = {},
    check_parsed_response_not_none: bool = True,
    max_retries: int = 5,
    base_interval_sec: int = 5,
):
    llm_contents = [Part.from_text(text=prompt)]
    for image_path in images:
        with open(image_path, "rb") as f:
            img_bytes = f.read()
        llm_contents.append(Part.from_bytes(data=img_bytes, mime_type="image/png"))
    return call_gemini_with_contents(
        contents=llm_contents,
        model_name=model_name,
        generation_configs=generation_configs,
        result_parsing_func=result_parsing_func,
        check_parsed_response_not_none=check_parsed_response_not_none,
        max_retries=max_retries,
        base_interval_sec=base_interval_sec,
    )


def call_llm_with_images(
    prompt: str,
    images: list,
    model_name: str,
    generation_configs=None,
    check_parsed_response_not_none: bool = True,
    return_json: bool = True,
    result_parsing_func=None,
):
    """Same contract as utils/llm_backend_utils.call_llm_with_images."""
    parser = (
        result_parsing_func
        if result_parsing_func is not None
        else (parse_gemini_json_results if return_json else identity_parse)
    )
    return call_gemini_with_images(
        prompt=prompt,
        images=images,
        model_name=model_name,
        result_parsing_func=parser,
        generation_configs=generation_configs or {},
        check_parsed_response_not_none=check_parsed_response_not_none,
    )
