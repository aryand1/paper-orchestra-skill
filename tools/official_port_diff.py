#!/usr/bin/env python3
"""Write docs/official-port.diff: every change the port makes to Google's files.

Usage: python tools/official_port_diff.py /path/to/google-research/paper-orchestra
"""
import difflib, os, sys

PAIRS = [
    ("methods/agents/outline_agent.py", "outline_agent.py"),
    ("methods/agents/literature_review_agent.py", "literature_review_agent.py"),
    ("methods/agents/section_writing_agent.py", "section_writing_agent.py"),
    ("methods/agents/content_refinement_agent.py", "content_refinement_agent.py"),
    ("methods/agents/plotting_agent.py", "plotting_agent.py"),
    ("methods/paper_writer.py", "paper_writer.py"),
    ("methods/paper_writer_with_plotting.py", "paper_writer_with_plotting.py"),
    ("methods/prompts/outline_agent.py", "prompts/outline_agent.py"),
    ("methods/prompts/literature_review_agent.py", "prompts/literature_review_agent.py"),
    ("methods/prompts/section_writing_agent.py", "prompts/section_writing_agent.py"),
    ("methods/prompts/content_refinement_agent.py", "prompts/content_refinement_agent.py"),
    ("utils/prompt_utils.py", "prompt_utils.py"),
    ("utils/common_utils.py", "common_utils.py"),
    ("utils/paper_banana_utils.py", "paper_banana_utils.py"),
    ("utils/scholar_utils.py", "scholar_utils.py"),
    ("utils/pdf_utils.py", "pdf_utils.py"),
    ("utils/gemini_utils.py", "llm.py"),
    ("autoraters/agent_review.py", "agent_review.py"),
]

def main(official):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    port = os.path.join(root, "skills", "paper-orchestra", "scripts", "po_official")
    out, stats = [], []
    for o, p in PAIRS:
        a = open(os.path.join(official, o), encoding="utf-8").read().splitlines(keepends=True)
        b = open(os.path.join(port, p), encoding="utf-8").read().splitlines(keepends=True)
        d = list(difflib.unified_diff(a, b, f"official/{o}", f"po_official/{p}", n=2))
        added = sum(1 for l in d if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in d if l.startswith("-") and not l.startswith("---"))
        stats.append((o, p, len(a), added, removed))
        out.extend(d)
    head = ["# Every change the port makes to google-research/paper-orchestra@d9bae99\n",
            "# Regenerate: python tools/official_port_diff.py /path/to/paper-orchestra\n#\n",
            "# file | official lines | +added | -removed\n"]
    head += [f"#   {o} -> {p} | {n} | +{ad} | -{rm}\n" for o, p, n, ad, rm in stats]
    with open(os.path.join(root, "docs", "official-port.diff"), "w", encoding="utf-8") as f:
        f.writelines(head + ["\n"] + out)
    for row in stats:
        print("%-46s %-34s %5d  +%-4d -%d" % row)

if __name__ == "__main__":
    main(sys.argv[1])
