"""Parity checks between the port (po_official/) and an official checkout.

Set PO_OFFICIAL_REPO to a clone of google-research/paper-orchestra (the port
was made from d9bae99). Skipped when it is not set. Run:
  PO_OFFICIAL_REPO=/path/to/paper-orchestra python3 skills/paper-orchestra/scripts/tests/test_po_official_parity.py
"""

import ast
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
OFFICIAL = os.environ.get("PO_OFFICIAL_REPO")

from po_official import prompt_utils  # noqa: E402
from po_official.prompts import (  # noqa: E402
    outline_agent as p_outline,
    literature_review_agent as p_lit,
    section_writing_agent as p_sec,
    content_refinement_agent as p_ref,
)
from po_official import paper_banana_utils as pb, agent_review as ar  # noqa: E402
from po_official.literature_review_agent import HybridLiteratureAgent  # noqa: E402


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _official_constants(relpath, names, extra_ns=None):
    """Evaluate selected module-level constants from an official source file."""
    src = _read(os.path.join(OFFICIAL, relpath))
    tree = ast.parse(src)
    ns = dict(extra_ns or {})
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets):
            exec(compile(ast.Module([node], []), relpath, "exec"), ns)
        elif isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module([node], []), relpath, "exec"), ns)
    return ns


def _official_methods(relpath, cls_name, names, ns):
    src = _read(os.path.join(OFFICIAL, relpath))
    for node in ast.parse(src).body:
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            body = [n for n in node.body if isinstance(n, ast.FunctionDef) and n.name in names]
            cls = ast.ClassDef(name=cls_name, bases=[], keywords=[], body=body, decorator_list=[])
            exec(compile(ast.fix_missing_locations(ast.Module([cls], [])), relpath, "exec"), ns)
            return ns[cls_name]
    raise AssertionError(cls_name)


@unittest.skipUnless(OFFICIAL and os.path.isdir(OFFICIAL or ""), "set PO_OFFICIAL_REPO")
class PromptParity(unittest.TestCase):
    def test_agent_prompts_identical(self):
        leak = _official_constants("utils/prompt_utils.py", {"UNIVERSAL_NO_LEAKAGE_PROMPT"})
        self.assertEqual(prompt_utils.UNIVERSAL_NO_LEAKAGE_PROMPT, leak["UNIVERSAL_NO_LEAKAGE_PROMPT"])
        ns = {"UNIVERSAL_NO_LEAKAGE_PROMPT": leak["UNIVERSAL_NO_LEAKAGE_PROMPT"]}
        for rel, mod, name in [
            ("methods/prompts/outline_agent.py", p_outline, "outline_agent_system_prompt"),
            ("methods/prompts/literature_review_agent.py", p_lit, "literature_review_agent_writter_prompt"),
            ("methods/prompts/section_writing_agent.py", p_sec, "section_writing_agent_prompt"),
            ("methods/prompts/content_refinement_agent.py", p_ref, "content_refinement_agent_system_prompt"),
        ]:
            got = _official_constants(rel, {name}, ns)[name]
            self.assertEqual(getattr(mod, name), got, rel)

    def test_paperbanana_prompts_identical(self):
        names = {n for n in dir(pb) if n.endswith("_SYSTEM_PROMPT")}
        self.assertGreaterEqual(len(names), 10)
        got = _official_constants("utils/paper_banana_utils.py", names)
        for n in names:
            self.assertEqual(getattr(pb, n), got[n], n)

    def test_agentreview_prompts_identical(self):
        names = {"SCORE_CALCULATION", "AGENT_REVIEW_RUBRICS", "AGENTREVIEW_INSTRUCTIONS",
                 "meta_reviewer_system_prompt", "get_agentreview_system_prompt"}
        got = _official_constants("autoraters/agent_review.py", names)
        for n in ["SCORE_CALCULATION", "AGENT_REVIEW_RUBRICS", "AGENTREVIEW_INSTRUCTIONS", "meta_reviewer_system_prompt"]:
            self.assertEqual(getattr(ar, n), got[n], n)
        for flags in [(True, True, True), (False, True, False), (True, False, True)]:
            self.assertEqual(ar.get_agentreview_system_prompt(*flags), got["get_agentreview_system_prompt"](*flags))


@unittest.skipUnless(OFFICIAL and os.path.isdir(OFFICIAL or ""), "set PO_OFFICIAL_REPO")
class LiteratureParity(unittest.TestCase):
    def setUp(self):
        import datetime, re
        from typing import List, Optional, Dict
        from pydantic import BaseModel
        from po_official import literature_review_agent as port_mod
        ns = {"re": re, "datetime": datetime, "List": List, "Optional": Optional, "Dict": Dict,
              "BaseModel": BaseModel, "PaperData": port_mod.PaperData}
        names = {"_collect_search_tasks", "_inject_citations_into_outline", "_is_paper_allowed",
                 "_generate_key", "_generate_bibtex", "_generate_citation_map", "_normalize_title"}
        self.Official = _official_methods("methods/agents/literature_review_agent.py",
                                          "HybridLiteratureAgent", names, ns)
        self.PaperData = port_mod.PaperData
        self.port = HybridLiteratureAgent.__new__(HybridLiteratureAgent)
        self.off = self.Official()

    def _outline(self):
        return {"intro_related_work_plan": {
                    "introduction_strategy": {"hook_hypothesis": "h", "problem_gap_hypothesis": "g",
                                              "search_directions": ["a", "b"]},
                    "related_work_strategy": {"subsections": [
                        {"subsection_title": "S1", "methodology_cluster": "c1", "limitation_search_queries": ["q1"]},
                        {"subsection_title": "S2", "methodology_cluster": "c2"}]}},
                "section_plan": [{"section_title": "Method", "subsections": [
                    {"subsection_title": "M1", "content_bullets": ["x", "y"], "citation_hints": ["h1", "h2"]}]}]}

    def test_tasks_and_injection(self):
        self.assertEqual(self.port._collect_search_tasks(self._outline()),
                         self.off._collect_search_tasks(self._outline()))
        reg = {"t1": self.PaperData(citation_key="K1", title="T1", authors=["A"], venue="V", year=2020,
                                    abstract="x", citation_count=1, found_in_section="Introduction", reason="r"),
               "t2": self.PaperData(citation_key="K2", title="T2", authors=["B"], venue="V", year=2021,
                                    abstract="x", citation_count=1, found_in_section="Method - M1", reason="r")}
        self.assertEqual(json.dumps(self.port._inject_citations_into_outline(self._outline(), reg), sort_keys=True),
                         json.dumps(self.off._inject_citations_into_outline(self._outline(), reg), sort_keys=True))

    def test_cutoff_keys_bibtex(self):
        cases = [({"publicationDate": "2024-10-31", "year": 2024}, "2024-11"),
                 ({"publicationDate": "2024-11-01", "year": 2024}, "2024-11"),
                 ({"publicationDate": None, "year": 2024}, "2024-11"),
                 ({"publicationDate": "2025-01-01", "year": 2025}, "2024-11"),
                 ({"year": None}, "2024-11"), ({"publicationDate": "2024-11"}, "2024-11-15")]
        for s2, cut in cases:
            self.assertEqual(self.port._is_paper_allowed(s2, cut), self.off._is_paper_allowed(s2, cut), (s2, cut))
        for authors, year, title in [([{"name": "Ada Lovelace"}], 1843, "Notes on the Analytical Engine"),
                                     ([], None, "The Study of a Thing"), (["Bob O'Neil"], 2020, "A-B: C & D")]:
            self.assertEqual(self.port._generate_key(authors, year, title), self.off._generate_key(authors, year, title))
        mk = lambda key, j=None: self.PaperData(citation_key=key, title="T", authors=["A B"], venue="V", year=2020,
                                                abstract=" a ", citation_count=0, found_in_section="s", reason="r",
                                                journal=j, volume="1" if j else None, pages="1-2" if j else None)
        a = [mk("K"), mk("K"), mk("K", "J")]
        b = [mk("K"), mk("K"), mk("K", "J")]
        self.assertEqual(self.port._generate_bibtex(a), self.off._generate_bibtex(b))
        self.assertEqual(self.port._generate_citation_map(a), self.off._generate_citation_map(b))


class FuzzyFallback(unittest.TestCase):
    def test_short_title_is_rejected_like_official(self):
        # Official threshold: fuzz.ratio + 10 (exact year) must exceed 70.
        from po_official import scholar_utils as su
        r = su.fuzz.ratio("an image is worth 16x16 words",
                          "an image is worth 16x16 words: transformers for image recognition at scale")
        self.assertLessEqual(r + 10, 70)


if __name__ == "__main__":
    unittest.main(verbosity=2)
