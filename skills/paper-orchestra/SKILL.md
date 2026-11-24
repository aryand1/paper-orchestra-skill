---
name: paper-orchestra
description: Run the PaperOrchestra pipeline (Song et al., 2026, arXiv:2604.05018) to turn research materials (idea, experimental log, LaTeX template, conference guidelines, optional figures) into a submission-ready LaTeX paper and PDF. Default "official mode" runs a port of Google's released code (google-research/paper-orchestra) with the host agent answering each model call, so stage order, retries, the AgentReview reviewer and the accept/revert rules match Google's implementation. TRIGGER when the user asks to "write a paper from my experiments", "turn this idea and these results into a paper", "generate a conference submission", "run paper-orchestra on X", or otherwise wants the end-to-end paper-writing pipeline.
data_access_level: raw
---

> **Paths.** Paths that start with `skills/`, `docs/`, `tools/` or `examples/` are relative to the
> root of the paper-orchestra-skill repository, not to the user's project. The root is the folder that
> contains `skills/shared/official-call-protocol.md`: two levels above this file's real location
> (resolve symlinks and junctions), or `PAPERORCHESTRA_REPO` in `~/.paperorchestra/config` (written by
> `install.py`). Run helper scripts as `python "<root>/skills/..."` and keep `workspace/` in the user's project.

# paper-orchestra (Orchestrator)

> Source paper: Song et al., *PaperOrchestra: A Multi-Agent Framework for
> Automated AI Research Paper Writing*, arXiv:2604.05018, 2026.
> Official code: <https://github.com/google-research/paper-orchestra> (Apache-2.0).
> The port in `scripts/po_official/` was made from commit `d9bae99`.

This skill has two modes:

| Mode | When | Who runs the control flow |
|---|---|---|
| **Official** (default) | Always, unless the user asks for extended mode | Google's pipeline code, ported in `scripts/po_official/`. You only answer the model calls it hands you. |
| **Extended** (opt-in) | The user asks for "extended mode", "the extras", or names one (devil's-advocate reviewer, claim-evidence gate, outline reconciliation, ...), or `PO_MODE=extended` | You, following `references/extended-mode.md` (the v0.3 pipeline of this pack). |

## Inputs

Put these in `workspace/inputs/` (the same files in both modes):

| File | Required | Notes |
|---|---|---|
| `idea.md` | yes | Idea summary (Sparse or Dense). Official default file name is `idea_sparse.md`; this pack uses `idea.md` (change with `--idea-filename`). |
| `experimental_log.md` | yes | Setup, raw numbers, observations. |
| `template.tex` | yes | Conference LaTeX template with `\section{...}` commands. Put its style files (`.sty`, `.cls`, `.bst`, extra `.tex`) next to it. |
| `conference_guidelines.md` | yes | Page limit, formatting rules (copied to the official `guidelines.md`). |
| `figures/` | no | Your own figures. With plotting off (the official default), add `figures/info.json`: `[{"name": "fig.png", "caption": "..."}]`. Without it the section writer sees no figures, as in the official code. |

Alternatively pass `--latex-template-dir` pointing at an official-style template folder (for example `templates/cvpr2025` from Google's repo), which holds `template.tex`, `guidelines.md` and style files.

If `idea.md` or `experimental_log.md` is missing and the user names a folder of notes or agent history, run the `agent-research-aggregator` skill first to create them.

## Official mode

### One-time setup (user's machine)

```bash
pip install pydantic requests pypdf pillow        # required
pip install matplotlib                            # only for --use-plotting
pip install thefuzz pymupdf json_repair           # optional; same results, closer to official
```

You also need `pdflatex` and `bibtex` (for example TeX Live), and either `pymupdf` or poppler's `pdftoppm` (for the page images in the formatting pass). No model API key is needed. A free `SEMANTIC_SCHOLAR_API_KEY` raises the citation-check rate limit but is optional.

### Run loop

1. Start or continue the run:

   ```bash
   python skills/paper-orchestra/scripts/po.py advance --workspace workspace/
   ```

   Options take effect on the first `advance` only (they are saved in `workspace/.po/config.json`):

   | Option | Official default | Meaning |
   |---|---|---|
   | `--use-plotting true` | `false` | Generate figures with the plotting agent instead of using `inputs/figures/`. |
   | `--research-cutoff 2024-11` | current year-month | Papers on or after the first day of this month are excluded. Set it for a fixed submission date. |
   | `--latex-template-dir DIR` | (built from `inputs/`) | Official-style template folder. |
   | `--idea-filename NAME` | `idea.md` here | Idea file inside `inputs/`. |

2. Read the JSON it prints.
   - `"status": "pending"`: answer every call in `calls` by following
     `skills/shared/official-call-protocol.md` (one fresh sub-agent per call,
     run them in parallel, web search only for calls whose `tools` include
     `web_search`). Then go back to step 1.
   - `"status": "done"`: the paper is at `workspace/final/paper.pdf` and
     `workspace/final/paper.tex`. Report it (see below).
   - `"status": "failed"`: all 3 tries failed, as in the official code. Show
     the user the `failures` list and `workspace/.po/run.log`.
   - `"status": "error"`: setup problem or changed inputs. Show the message.
     After the user fixes inputs, `po.py reset --workspace workspace/` starts a
     new run (saved answers are deleted; caches are kept).

3. Do not edit prompt files, responses or pipeline outputs by hand, and do not
   skip, reorder or merge calls. The pipeline code decides what happens next.

`python skills/paper-orchestra/scripts/po.py status --workspace workspace/` shows progress at any time. Logs are in `workspace/.po/run.log`.

### What the pipeline does (Google's code, unchanged)

1. **Outline agent**: 1 call. Plotting plan, literature search plan, section plan with citation hints.
2. **Literature agent** (in parallel with plotting): one web-search discovery call per search direction, related-work query and citation hint (exploration asks for 10 to 15 papers, targeted hints for exactly 1; up to 3 tries each). Each candidate is checked against Semantic Scholar (top 3 results, fuzzy title ratio +10 for the same year must exceed 70, abstract required, published before the cutoff), de-duplicated by normalized title, turned into BibTeX and a citation map, and written back into the outline. Then 1 call writes the Introduction and Related Work, citing at least 90% of the pool.
3. **Plotting agent** (only with `--use-plotting true`): per figure, plan, style, render (matplotlib code for plots), up to 3 critic rounds, caption.
4. **Section writing agent**: 1 multimodal call writes the remaining sections into the template.
5. **Content refinement agent**: compiles the draft, then up to 3 rounds of AgentReview review (3 independent reviews plus a meta-review; scores averaged) and revision. A revision is kept if Overall rises, or ties with sub-axis gains at least as large as drops; otherwise it is reverted and the loop stops. A compile failure skips that round.
6. **Formatting pass**: 1 call checks page images against the guidelines; if it finds issues, 1 call fixes the LaTeX, kept if it compiles.

### Reporting

Tell the user the path to `workspace/final/paper.pdf`, the number of references (`workspace/final/references.bib`), and the refinement outcome per round from `refinement_worklog` in the `done` JSON. Remind them to check every claim and citation.

### Known differences from Google's run

See `docs/official-alignment.md`. In short: the model is your host model, not Gemini; discovery uses your web search instead of Gemini's Google Search grounding; temperatures cannot be set; diagrams are drawn as matplotlib code because there is no image model; PaperBanana few-shot examples and style guides are used only if `PO_PAPERBANANA_DIR` points at a PaperBanana checkout (the released code also runs without them).

## Extended mode

Follow `references/extended-mode.md`. It uses the paper's prompts plus this pack's extra gates and steps (all paths in it are relative to this skill folder).

## Resources

- `scripts/po.py`: official-mode driver (`advance`, `status`, `reset`).
- `scripts/po_official/`: the port of Google's code (Apache-2.0; every changed file says what changed; full diff in `docs/official-port.diff`).
- `skills/shared/official-call-protocol.md`: how to answer a pending call.
- `scripts/tests/`: end-to-end test with a scripted host, and parity tests against an official checkout.
- `references/extended-mode.md`: the opt-in v0.3 pipeline.
- `references/paper-summary.md`, `references/io-contract.md`, `references/host-integration.md`.
