# agent-research-aggregator (optional)

A pre-pipeline skill that bridges the gap between **scattered AI coding-agent
history** and the structured `(idea.md, experimental_log.md)` inputs that
PaperOrchestra expects. If you have been running experiments through Claude
Code, Cursor, Antigravity, or OpenClaw — but never wrote up a clean experiment
log — this skill does that extraction for you.

**It is optional.** If `workspace/inputs/idea.md` and
`workspace/inputs/experimental_log.md` already exist, the skill skips itself
and the pipeline proceeds directly. It only runs when the inputs are missing or
when you explicitly point an agent at a directory.

The simplest way to use it: just tell your agent the folder. If you have a
directory (a project root, an agent cache, any folder with research notes), the
aggregator figures out what's inside and structures it for PaperOrchestra.
The first thing it does is aggregate — scanning, extracting, and synthesising —
so even if the data is scattered across multiple files and formats, it produces
clean, reviewable inputs before anything gets written.

Run it **before** `paper-orchestra` (or let `paper-orchestra` call it automatically
when inputs are missing).

## What it does

```
[.claude/]  [.cursor/]  [.antigravity/]  [.openclaw/]
      │            │              │               │
      └────────────┴──────────────┴───────────────┘
                        │
                Phase 1: Discovery  (deterministic)
                        │
                Phase 2: Extraction (LLM — per batch)
                        │
                Phase 3: Synthesis  (LLM — one call)
                        │
                Phase 4: Formatting (deterministic)
                        │
             ┌──────────┴──────────┐
      workspace/inputs/      workspace/ara/
        idea.md                aggregation_report.md
        experimental_log.md    discovered_logs.json
                               raw_experiments.json
                               synthesis.json
```

The four phases are:

| Phase | Tool | What happens |
|---|---|---|
| 1 Discovery | `discover_logs.py` | Walks `--search-roots` to catalog every relevant log file across all agent caches. Prints a summary for user review before anything is read. |
| 2 Extraction | LLM (per ~50 KB batch) | Applies `references/extraction-prompt.md` to each batch; produces `raw_experiments.json`. PII is stripped; unverified numbers are flagged `[UNVERIFIED]`. |
| 3 Synthesis | LLM (one call) | Merges possibly-redundant experiment records into a single research narrative (`synthesis.json`). Detects multiple disconnected projects and pauses to ask the user. |
| 4 Formatting | `format_po_inputs.py` | Converts `synthesis.json` into `idea.md` (Sparse Idea format, §3.1) and `experimental_log.md` (App. D.3), ready for `paper-orchestra`. |

## Integration

**Install** — no extra dependencies beyond the base `requirements.txt`.

`python install.py` installs it with the other skills (see the README).

For Cursor / Antigravity / Cline / Aider, follow the same per-host
instructions in `skills/paper-orchestra/references/host-integration.md`.

**Invoke** by telling your coding agent:

> "Aggregate my agent logs for paper writing" — or —
> "Prepare PaperOrchestra inputs from my cache" — or —
> "Turn my agent logs into a paper"

The trigger phrases are listed in the `description` field of
`skills/agent-research-aggregator/SKILL.md`.

## Parameters

| Flag | Default | Description |
|---|---|---|
| `--search-roots` | cwd, `~` | Directories to scan for agent caches |
| `--agents` | all | Subset: `claude,cursor,antigravity,openclaw` |
| `--workspace` | `./workspace` | PaperOrchestra workspace root |
| `--depth` | 4 | Max scan depth (prevents runaway traversal) |
| `--since` | — | Only logs modified after this date (ISO 8601) |

## Example workflows

**From Claude Code memory + CLAUDE.md only:**

```bash
python skills/agent-research-aggregator/scripts/discover_logs.py \
    --search-roots . \
    --agents claude \
    --out workspace/ara/discovered_logs.json
# → finds .claude/projects/<hash>/memory/*.md and CLAUDE.md
```

**From a Cursor project (chat history + rules):**

```bash
python skills/agent-research-aggregator/scripts/discover_logs.py \
    --search-roots ~/my-project \
    --agents cursor \
    --out workspace/ara/discovered_logs.json
# → finds .cursor/chat/chatHistory.json and .cursorrules
```

**From Antigravity worker logs, restricted to the last 60 days:**

```bash
python skills/agent-research-aggregator/scripts/discover_logs.py \
    --search-roots ~/my-project \
    --agents antigravity \
    --since 2026-02-09 \
    --out workspace/ara/discovered_logs.json
# → finds .antigravity/workers/<id>/log.jsonl and output.md
```

**From OpenClaw sessions + run metrics:**

```bash
python skills/agent-research-aggregator/scripts/discover_logs.py \
    --search-roots ~/my-project \
    --agents openclaw \
    --out workspace/ara/discovered_logs.json
# → finds .openclaw/sessions/*/conversation.md and runs/*/metrics.json
```

**Full run across all caches:**

```bash
# Phase 1 — discovery
python skills/agent-research-aggregator/scripts/discover_logs.py \
    --search-roots . ~ --out workspace/ara/discovered_logs.json

# Phase 2 — LLM extraction (your agent handles this; validate afterward)
python skills/agent-research-aggregator/scripts/extract_experiments.py \
    --discovered workspace/ara/discovered_logs.json \
    --out workspace/ara/raw_experiments.json --validate-only

# Phase 3 — LLM synthesis (your agent handles this)

# Phase 4 — format + audit report
python skills/agent-research-aggregator/scripts/format_po_inputs.py \
    --synthesis workspace/ara/synthesis.json \
    --out workspace/inputs/ \
    --report workspace/ara/aggregation_report.md
```

After Phase 4, the workspace is ready for `paper-orchestra`. You still need
to supply `workspace/inputs/template.tex` (your conference LaTeX template) and
`workspace/inputs/conference_guidelines.md` (page limit, deadline, formatting
rules).

## Reference docs

- [`skills/agent-research-aggregator/SKILL.md`](skills/agent-research-aggregator/SKILL.md) — full phase-by-phase protocol
- [`skills/agent-research-aggregator/references/log-formats.md`](skills/agent-research-aggregator/references/log-formats.md) — per-agent cache layouts and file priorities
- [`skills/agent-research-aggregator/references/extraction-prompt.md`](skills/agent-research-aggregator/references/extraction-prompt.md) — verbatim LLM extraction prompt
- [`skills/agent-research-aggregator/references/synthesis-prompt.md`](skills/agent-research-aggregator/references/synthesis-prompt.md) — verbatim LLM synthesis prompt
