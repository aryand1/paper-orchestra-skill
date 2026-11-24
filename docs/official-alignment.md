# Alignment with Google's official PaperOrchestra code

Reference: [google-research/paper-orchestra](https://github.com/google-research/paper-orchestra)
at commit `d9bae99` (Nov 15, 2026), Apache-2.0.

Official mode (`skills/paper-orchestra/scripts/po.py`) runs a port of that
code. This page lists what matches, what cannot match without a Gemini key, and
the small engineering changes the port needs. Every changed line is in
[`official-port.diff`](official-port.diff).

## How the port works

Google's code is kept almost line for line in
`skills/paper-orchestra/scripts/po_official/`. Only the Gemini client
(`utils/gemini_utils.py`) is replaced, by `llm.py`, which keeps the same
function signatures and the same response parsers. When the pipeline makes a
model call, `llm.py` writes the exact model input (system instruction, text
parts, attached images or PDF, tools) to a prompt file and stops. The host
agent answers it in a fresh sub-agent and writes the reply to a response file.
The next `po.py advance` replays the pipeline from the start, reusing saved
replies, so Google's code decides every step.

To make replays identical, the port names each branch of the pipeline (call
ids such as `a1/refine/review_v1/ens2/c01.r1`), caches Semantic Scholar
results and compiled PDFs, and pins Python's hash seed. If an input changes
after a call was answered, `advance` stops with an error instead of mixing
runs.

## What matches the official code

| Part | Status |
|---|---|
| All prompts (outline, literature writer, section writing, refinement, anti-leakage, PaperBanana retriever/planner/stylist/visualizer/critic, AgentReview persona, rubric, instructions, meta-review, and every inline prompt) | Identical strings (parity test) |
| Response parsing and retries | Official parsers copied verbatim; up to 5 tries when a reply cannot be parsed |
| Pipeline | Same stage order, file layout and copies; whole run retried up to 3 times on failure |
| Outline agent | Same input parts and order, cutoff in the prompt, 1 call |
| Literature agent | Same search tasks (Introduction directions, related-work queries, and one targeted search per citation hint); same discovery prompts; 3 tries per task; Semantic Scholar top 3, fuzzy ratio +10 for the same year must exceed 70, abstract required, both date filters; abstracts cut at 1,500 characters; dedup by normalized title; same citation keys, BibTeX, citation map, outline write-back and writer payload |
| Plotting agent | Same plan, style, render, up to 3 critic rounds (stops on "No changes needed."), caption cleanup, JPEG at 300 dpi, `figures/<figure_id>.jpg`, `figures/info.json` |
| Section writing agent | Same prompt assembly, reference library format, figure list, raster figures attached |
| Content refinement | Same AgentReview ensemble (3 reviews, meta-review, rounded mean scores), same refinement input (PDF + worklog + review + guidelines + log + citation map + LaTeX), same accept/revert rules, compile failure skips the round, up to 3 rounds |
| Formatting pass | Same page-grid check against the guidelines and 1 fix call, kept if it compiles |
| LaTeX build | `pdflatex`, `bibtex`, `pdflatex`, `pdflatex`, 30 s timeout per step |
| Defaults | Plotting off; research cutoff = current year-month; 3 critic rounds; 3 refinement rounds |

## What cannot match without a Gemini key

1. **The model.** The official code uses `gemini-3.1-pro-preview` for writing
   and review and `gemini-3-flash-preview` for discovery, with set
   temperatures (for example 0.75 for reviewers, 0.7 for section writing).
   Here your host model answers, and temperatures cannot be set. Each prompt
   file records the official model and settings.
2. **Paper discovery.** The official code uses Gemini's Google Search
   grounding. Here the discovery calls are answered with your host's web
   search.
3. **Diagrams.** The official code renders diagrams with
   `gemini-3-pro-image-preview`. Here the same prompt is sent with a marked
   `[HOST ADAPTER]` note asking for matplotlib code, which is then rendered.
   Statistical plots are generated as matplotlib code in both.
4. **Call independence.** Each Gemini call is independent. The port relies on
   the host answering each call in a fresh sub-agent.

## Small engineering changes

These keep behavior the same or are needed to run inside a coding agent:

- Thread pools (discovery, figures, literature and plotting in parallel) run as
  named branches; all their pending calls are reported together so the host
  can answer them in parallel. Candidates are checked against Semantic Scholar
  in task order (official: thread completion order, which varies run to run).
- Python's hash seed is fixed, because the official code orders some lists
  with `list(set(...))`.
- Semantic Scholar results are cached, including failures, so replays see the
  same results. The 1 s wait is skipped for cached lookups. The API key is no
  longer printed to the log.
- Generated plot code runs in a separate Python process instead of `exec()`
  inside the pipeline.
- Compiled PDFs are cached by input hash, and `SOURCE_DATE_EPOCH` is set so
  rebuilt PDFs are byte-identical.
- Page grids are composed with Pillow (rendered by PyMuPDF if installed,
  otherwise poppler's `pdftoppm`) instead of OpenCV.
- `thefuzz`, `json_repair`, `pymupdf` and `python-dotenv` are optional. The
  fallback fuzzy ratio gave the same score as `thefuzz` on 4,000 random title
  pairs.
- Inputs: `workspace/inputs/` is split into the official `raw_materials/`
  folder and a template folder. The idea file defaults to `idea.md` (official
  CLI: `idea_sparse.md`).
- Not ported: the batch benchmark runner, the Streamlit demo, and the
  autorater scripts other than AgentReview (the `paper-autoraters` skill keeps
  the prompts, now aligned with the code).

## Official behaviors worth knowing (kept as-is)

- **Short titles can fail the match.** If discovery returns a shortened title,
  the fuzzy ratio against the full Semantic Scholar title may not exceed 70.
  For example, "An Image is Worth 16x16 Words" scores 66 with the year bonus
  against the full ViT title, so the paper is dropped.
- **First found wins.** A paper found first by an Introduction search is not
  also listed for the section whose citation hint targeted it.
- **Silent drops.** Semantic Scholar errors (such as rate limits) drop the
  candidate. A free `SEMANTIC_SCHOLAR_API_KEY` makes this less likely.
- **No figures by default.** Plotting is off. Without `inputs/figures/info.json`
  the section writer sees no figures.
- **Cutoff.** The research cutoff defaults to the current month. Set
  `--research-cutoff` for a fixed submission date.
- **Limitations.** The section-writing prompt calls discussing limitations
  "nice to have", while the refinement prompt says "Never explicitly state a
  limitation" (to stop the agent from gaming the reviewer score).
- **Unused prompt.** The official repo defines a `format_agent` prompt that no
  code uses; the formatting pass uses inline prompts, as ported.

## Extended mode

Extended mode (`skills/paper-orchestra/references/extended-mode.md`) is the
v0.3 pipeline of this pack. It is opt-in and adds steps that the official code
does not have: pre-flight gates, Step 3.5 outline reconciliation,
claim-evidence and lint gates, a 6-axis 0 to 100 reviewer rubric with a
devil's-advocate reviewer, decision bands with target and plateau halts, a
hard check on the word "limitation", and Crossref/OpenAlex cross-checks.

## Verification

- `skills/paper-orchestra/scripts/tests/test_po_official_parity.py`: with
  `PO_OFFICIAL_REPO` set to an official checkout, checks that every prompt
  string is identical and that the literature helpers (search tasks, outline
  write-back, cutoff filter, keys, BibTeX, citation map) give the same output.
- `skills/paper-orchestra/scripts/tests/test_po_official_e2e.py`: drives the
  whole pipeline with a scripted host and mocked Semantic Scholar, with and
  without plotting, including real LaTeX builds. It checks parse retries,
  discovery retries, the citation filters, the reviewer ensemble, the
  accept/revert path, the formatting pass and replay stability.
- `docs/official-port.diff`: every changed line, regenerated with
  `python tools/official_port_diff.py /path/to/paper-orchestra`.
