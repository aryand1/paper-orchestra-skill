"""End-to-end test of official mode (po.py) with a scripted host and mocked Semantic Scholar.

Needs pdflatex + bibtex, pypdf, Pillow, pydantic, requests; plotting test also
needs matplotlib. Run:  python3 skills/paper-orchestra/scripts/tests/test_po_official_e2e.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PO = os.path.join(HERE, "..", "po.py")
sys.path.insert(0, HERE)
import fake_host  # noqa: E402

TEMPLATE = r"""\documentclass{article}
\usepackage{graphicx}
\usepackage{booktabs}
\begin{document}
\title{Anonymous Submission}\maketitle
\section{Introduction}
\section{Related Work}
\section{Method}
\section{Experiments}
\section{Conclusion}
\bibliographystyle{plain}
\bibliography{references}
\end{document}
"""


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _have(cmd):
    return shutil.which(cmd) is not None


class OfficialModeE2E(unittest.TestCase):
    def _make_ws(self, figures=False):
        ws = tempfile.mkdtemp(prefix="po_e2e_")
        inp = os.path.join(ws, "inputs")
        os.makedirs(inp)
        _write(os.path.join(inp, "idea.md"), "# Idea\nA ViT adapter that improves accuracy.\n")
        _write(os.path.join(inp, "experimental_log.md"), "# Log\n| Method | Acc |\n|---|---|\n| Ours | 91.2 |\n| Baseline | 88.0 |\n")
        _write(os.path.join(inp, "template.tex"), TEMPLATE)
        _write(os.path.join(inp, "conference_guidelines.md"), "Page limit: 8 pages.\n")
        mock = os.path.join(ws, "s2_mock.json")
        _write(mock, json.dumps(fake_host.S2_MOCK))
        return ws, mock

    def _advance(self, ws, mock, extra=()):
        env = dict(os.environ, PO_S2_MOCK_FILE=mock)
        out = subprocess.run([sys.executable, PO, "advance", "--workspace", ws,
                              "--research-cutoff", "2024-11", *extra],
                             capture_output=True, text=True, env=env, timeout=600)
        try:
            return json.loads(out.stdout)
        except json.JSONDecodeError:
            raise AssertionError(f"bad stdout:\n{out.stdout}\n{out.stderr}")

    def _run_to_end(self, ws, mock, extra=()):
        seen = []
        for _ in range(80):
            res = self._advance(ws, mock, extra)
            if res["status"] != "pending":
                return res, seen
            for call in res["calls"]:
                seen.append(call)
                with open(call["response_file"], "w", encoding="utf-8") as f:
                    f.write(fake_host.answer(call))
        self.fail("pipeline did not finish in 80 advances")

    @unittest.skipUnless(_have("pdflatex") and _have("bibtex"), "needs pdflatex and bibtex")
    def test_no_plotting_run(self):
        ws, mock = self._make_ws()
        res, calls = self._run_to_end(ws, mock)
        self.assertEqual(res["status"], "done", res)
        self.assertTrue(os.path.exists(os.path.join(ws, "final", "paper.pdf")))
        ids = [c["id"] for c in calls]

        # Official parse-retry: the first outline reply had no JSON, so r2 was issued.
        self.assertIn("a1/outline/c01.r1", ids)
        self.assertIn("a1/outline/c01.r2", ids)
        # Official 3-try discovery retry: an empty candidate list triggers a second search call.
        retry = [i for i in ids if "/lit/discovery/" in i and i.endswith("/c02.r1")]
        self.assertEqual(len(retry), 1, ids)
        # Discovery calls are marked for web search (official: Google Search grounding).
        disc = [c for c in calls if "/lit/discovery/" in c["id"]]
        self.assertTrue(all(c["tools"] == ["web_search"] for c in disc))
        # Targeted search for the outline's citation hint exists alongside exploration searches.
        self.assertEqual(len({c["id"].split("/c0")[0] for c in disc}), 4)

        # Literature filters: future paper cut by cutoff, empty-abstract paper dropped, duplicate merged.
        bib = _read(os.path.join(ws, "literature_agent_output", "references.bib"))
        for key in ["Dosovitskiy2020ImageWorth", "Touvron2021TrainingDataefficient", "He2020MomentumContrast"]:
            self.assertIn(key, bib)
        self.assertNotIn("Future", bib)
        self.assertNotIn("Houlsby", bib)
        self.assertEqual(bib.count("@inproceedings"), 3)
        outline = json.loads(_read(os.path.join(ws, "outline.json")))
        sub = outline["section_plan"][0]["subsections"][0]
        self.assertNotIn("citation_hints", sub)
        # Official dedup is first-found-wins: ViT was found first by an Introduction search,
        # so the later targeted hit for Method is a duplicate and Method gets no candidates.
        self.assertEqual(sub["citation_candidates"], [])
        intro = outline["intro_related_work_plan"]["introduction_strategy"]["citation_candidates"]
        self.assertEqual(sorted(intro), ["Dosovitskiy2020ImageWorth", "He2020MomentumContrast",
                                         "Touvron2021TrainingDataefficient"])

        # Reviewer: 3 ensemble reviews + 1 meta review per version.
        for label in ["initial", "v1", "v2"]:
            self.assertEqual(len([i for i in ids if f"/review_{label}/ens" in i]), 3, label)
            self.assertEqual(len([i for i in ids if f"/review_{label}/meta/" in i]), 1, label)
        self.assertFalse(any("/review_v3/" in i for i in ids))

        # Official accept/revert: v1 accepted (Overall up), v2 rejected (tie, sub-axis drop), loop stops.
        wl = json.loads(_read(os.path.join(ws, "content_refinement_workdir", "content_refinement_worklog.json")))
        self.assertEqual(wl["v1"]["outcome"], "ACCEPTED_SCORE_INCREASE")
        self.assertEqual(wl["v2"]["outcome"], "REJECTED_DEGRADATION")
        self.assertNotIn("v3", wl)
        final_tex = _read(os.path.join(ws, "final", "paper.tex"))
        self.assertIn("Revision 1.", final_tex)
        self.assertNotIn("Revision 2.", final_tex)
        # Formatting pass ran and its compiled fix was kept.
        self.assertIn("\\vspace{2pt}", final_tex)
        fmt = json.loads(_read(os.path.join(ws, "content_refinement_workdir", "format_refinement_worklog.json")))
        self.assertEqual(fmt["v1"]["outcome"], "ACCEPTED_COMPILE_SUCCESS")

        # Replay is stable: advancing a finished run issues no new calls.
        again = self._advance(ws, mock)
        self.assertEqual(again["status"], "done")
        shutil.rmtree(ws)

    @unittest.skipUnless(_have("pdflatex") and _have("bibtex"), "needs pdflatex and bibtex")
    def test_failed_compile_restarts_run(self):
        """Official 3-try loop: a draft that cannot compile fails try 1; try 2 starts over."""
        ws, mock = self._make_ws()
        real_answer = fake_host.answer

        def broken_first_try(call):
            if call["id"].startswith("a1/section/"):
                return "```latex\n\\documentclass{article}\\begin{document}\\input{missing_file_xyz}\\end{document}\n```"
            return real_answer(call)

        fake_host.answer = broken_first_try
        try:
            res, calls = self._run_to_end(ws, mock)
        finally:
            fake_host.answer = real_answer
        self.assertEqual(res["status"], "done", res)
        ids = [c["id"] for c in calls]
        self.assertTrue(any(i.startswith("a1/section/") for i in ids))
        self.assertTrue(any(i.startswith("a2/outline/") for i in ids))   # the run started over
        self.assertFalse(any(i.startswith("a1/refine/review_") for i in ids))  # try 1 never reached review
        state = json.loads(_read(os.path.join(ws, ".po", "state.json")))
        self.assertEqual(state["failed_tries"], 1)
        self.assertTrue(os.path.exists(os.path.join(ws, "final", "paper.pdf")))
        shutil.rmtree(ws)

    @unittest.skipUnless(_have("pdflatex") and _have("bibtex"), "needs pdflatex and bibtex")
    def test_plotting_run(self):
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            self.skipTest("needs matplotlib")
        ws, mock = self._make_ws()
        res, calls = self._run_to_end(ws, mock, extra=("--use-plotting", "true"))
        self.assertEqual(res["status"], "done", res)
        ids = [c["id"] for c in calls]
        fig_calls = sorted(i for i in ids if "/plot/" in i)
        # planner, stylist, render, critic(changes), re-render, critic(no changes), caption
        self.assertEqual([i.rsplit("/", 1)[1] for i in fig_calls],
                         ["c01.r1", "c02.r1", "c03.r1", "c04.r1", "c05.r1", "c06.r1", "c07.r1"])
        info = json.loads(_read(os.path.join(ws, "latex_writeup", "figures", "info.json")))
        self.assertEqual(info, [{"name": "fig_main_results.jpg",
                                 "caption": "Accuracy of our method compared with the baseline."}])
        self.assertTrue(os.path.exists(os.path.join(ws, "figures", "fig_main_results.jpg")))
        sec = [c for c in calls if "/section/" in c["id"]][0]
        self.assertEqual(len(sec["attachments"]), 1)  # figure image passed to the section writer
        shutil.rmtree(ws)


if __name__ == "__main__":
    unittest.main(verbosity=2)
