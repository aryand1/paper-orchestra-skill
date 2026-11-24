"""Scripted stand-in for the host agent, used by the end-to-end tests.

It answers each pending call by looking at the call id (which encodes the
pipeline stage) and the prompt text, and returns canned replies in the formats
the official parsers expect. Scores are scripted so the refinement loop takes a
known path: v1 is accepted (Overall rises), v2 is rejected (Overall ties but a
sub-axis drops), then the formatting pass runs.
"""

import json
import re

AXES = ["Originality", "Quality", "Clarity", "Significance", "Soundness", "Presentation", "Contribution"]

PAPER_BODY = r"""
\section{Method}
We describe the method. The core idea comes from the idea file.
\section{Experiments}
\begin{table}[t]\centering
\begin{tabular}{lc}\toprule Method & Acc \\ \midrule Ours & 91.2 \\ Baseline & 88.0 \\ \bottomrule\end{tabular}
\caption{Main results.}\label{tab:main}\end{table}
Our method reaches 91.2 accuracy.
%FIGURE%
\section{Conclusion}
We presented the method.
"""


def _template(intro_cites, body=""):
    cites = ",".join(intro_cites) if intro_cites else ""
    cite_cmd = f"\\cite{{{cites}}}" if cites else ""
    return (
        "\\documentclass{article}\n\\usepackage{graphicx}\n\\usepackage{booktabs}\n"
        "\\begin{document}\n\\title{Anonymous Submission}\\maketitle\n"
        f"\\section{{Introduction}}\nPrior work exists {cite_cmd}.\n"
        f"\\section{{Related Work}}\nRelated methods {cite_cmd}.\n"
        f"{body}\n\\bibliographystyle{{plain}}\n\\bibliography{{references}}\n\\end{{document}}\n"
    )


def _review(overall, sub):
    data = {"Summary": "A paper.", "Strengths": ["clear"], "Weaknesses": ["needs more detail on setup"],
            "Questions": ["What is the batch size?"], "Limitations": ["none stated"],
            "Ethical Concerns": False, "Overall": overall, "Confidence": 4, "Decision": "Reject"}
    for a in AXES:
        data[a] = sub.get(a, 2)
    return "THOUGHT:\nThe paper is fine.\n\nREVIEW JSON:\n```json\n" + json.dumps(data, indent=1) + "\n```\n"


REVIEW_PLAN = {
    "initial": (4, {}),
    "v1": (5, {}),
    "v2": (5, {"Clarity": 1}),
    "v3": (5, {}),
}


def answer(call):
    pid = call["id"]
    with open(call["prompt_file"], encoding="utf-8") as f:
        prompt = f.read()

    if "/outline/" in pid:
        if pid.endswith("c01.r1"):
            return "I could not produce JSON this time."  # forces an official parse-retry (r2)
        outline = {
            "plotting_plan": [{
                "figure_id": "fig_main_results", "title": "Main results", "plot_type": "plot",
                "data_source": "experimental_log.md", "objective": "Bar chart of accuracy.", "aspect_ratio": "16:9"}],
            "intro_related_work_plan": {
                "introduction_strategy": {"hook_hypothesis": "h", "problem_gap_hypothesis": "g",
                                          "search_directions": ["vision transformers", "self-supervised learning"]},
                "related_work_strategy": {"overview": "o", "subsections": [{
                    "subsection_title": "Adapters", "methodology_cluster": "adapters",
                    "sota_investigation_mission": "m", "limitation_hypothesis": "l",
                    "limitation_search_queries": ["parameter-efficient adapters"], "bridge_to_our_method": "b"}]}},
            "section_plan": [{"section_title": "Method", "subsections": [{
                "subsection_title": "Backbone", "content_bullets": ["we use a ViT backbone"],
                "citation_hints": ["research paper introducing the Vision Transformer (ViT)"]}]}],
        }
        return "```json\n" + json.dumps(outline, indent=1) + "\n```"

    if "/lit/discovery/" in pid:
        if "EXACTLY 1 candidate" in prompt:
            cands = [{"title": "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale", "year": 2020, "reason": "introduces ViT"}]
        elif "self-supervised learning" in prompt and pid.endswith("c01.r1"):
            cands = []  # forces the official 3-try discovery retry (c02)
        elif "self-supervised learning" in prompt:
            cands = [{"title": "Momentum Contrast for Unsupervised Visual Representation Learning", "year": 2020, "reason": "ssl"}]
        elif "vision transformers" in prompt:
            cands = [{"title": "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale", "year": 2020, "reason": "dup on purpose"},
                     {"title": "Training data-efficient image transformers", "year": 2021, "reason": "DeiT"},
                     {"title": "A Paper From The Future", "year": 2030, "reason": "must be cut by the cutoff"}]
        else:
            cands = [{"title": "Parameter-Efficient Transfer Learning for NLP", "year": 2019, "reason": "adapters"}]
        return "```json\n" + json.dumps({"section_name": "x", "candidates": cands}) + "\n```"

    if "/lit/writer/" in pid:
        m = re.search(r'"citation_checklist": (\[[^\]]*\])', prompt)
        keys = json.loads(m.group(1)) if m else []
        return "```latex\n" + _template(keys) + "```"

    if "/plot/" in pid:
        if "Detailed description of the target figure to be generated" in prompt:
            return "A bar chart with two bars: Ours 91.2 and Baseline 88.0."
        if "Style Guidelines:" in prompt:
            return "A clean bar chart, two bars, labeled axes, Ours 91.2 and Baseline 88.0."
        if "Use python matplotlib to generate a statistical plot" in prompt:
            return ("```python\nimport matplotlib.pyplot as plt\nfig, ax = plt.subplots(figsize=(4,3))\n"
                    "ax.bar(['Ours','Baseline'],[91.2,88.0])\nax.set_ylabel('Accuracy')\n```")
        if "Target Plot for Critique:" in prompt:
            if "/c04." in pid:  # first critic round asks for a change
                return json.dumps({"critic_suggestions": "Add y-axis label units.",
                                   "revised_description": "Bar chart, y-axis 'Accuracy (%)', Ours 91.2, Baseline 88.0."})
            return json.dumps({"critic_suggestions": "No changes needed.", "revised_description": "No changes needed."})
        if "Please provide the final caption" in prompt:
            return "Caption: Accuracy of our method compared with the baseline."
        raise AssertionError(f"unexpected plot call {pid}")

    if "/section/" in pid:
        m = re.search(r"Filename: (\S+)", prompt)
        fig = (f"\\begin{{figure}}[t]\\centering\\includegraphics[width=\\linewidth]{{figures/{m.group(1)}}}"
               "\\caption{Accuracy.}\\end{figure}") if m else ""
        keys = re.findall(r"--- Key: (\S+) ---", prompt)
        return "```latex\n" + _template(keys, PAPER_BODY.replace("%FIGURE%", fig)) + "```"

    if "/refine/review_" in pid:
        label = re.search(r"/review_([a-z0-9]+)/", pid).group(1)
        overall, sub = REVIEW_PLAN[label]
        if "/meta/" in pid:
            return _review(overall, sub)
        return _review(overall, sub)

    if "/refine/refine_iter" in pid:
        latex = re.search(r"--- CURRENT LATEX SOURCE ---\n(.*?)\n\nINSTRUCTION:", prompt, re.S).group(1)
        it = re.search(r"refine_iter(\d+)", pid).group(1)
        latex = latex.replace("We presented the method.", f"We presented the method. Revision {it}.")
        return ("```json\n" + json.dumps({"changes": [f"revision {it}"]}) + "\n```\n```latex\n" + latex + "\n```")

    if "/refine/format_review/" in pid:
        return "```json\n" + json.dumps({"figure_and_tables": {"Table 1": {"detected_issue": "Caption is too close.",
                                                                              "suggested_fix": "Add vspace."}},
                                          "other_issues": []}) + "\n```"

    if "/refine/format_fix/" in pid:
        latex = re.search(r"--- CURRENT LATEX SOURCE ---\n(.*?)\n\nOutput the Full Revised LaTeX only", prompt, re.S).group(1)
        return "```latex\n" + latex.replace("\\caption{Main results.}", "\\caption{Main results.}\\vspace{2pt}") + "\n```"

    raise AssertionError(f"unexpected call {pid}")


S2_MOCK = {
    "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale": [{"paperId": "vit", "title": "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale",
                                       "authors": [{"name": "Alexey Dosovitskiy"}], "venue": "ICLR", "year": 2020,
                                       "abstract": "We show that a pure transformer applied to image patches works well.",
                                       "citationCount": 40000, "journal": None, "publicationDate": "2020-10-22"}],
    "Training data-efficient image transformers": [{"paperId": "deit", "title": "Training data-efficient image transformers & distillation through attention",
                                                    "authors": [{"name": "Hugo Touvron"}], "venue": "ICML", "year": 2021,
                                                    "abstract": "We produce competitive convolution-free transformers.",
                                                    "citationCount": 6000, "journal": None, "publicationDate": "2020-12-23"}],
    "A Paper From The Future": [{"paperId": "fut", "title": "A Paper From The Future", "authors": [{"name": "Zed Future"}],
                                 "venue": "NeurIPS", "year": 2030, "abstract": "Too new.", "citationCount": 1,
                                 "journal": None, "publicationDate": "2030-01-01"}],
    "Momentum Contrast for Unsupervised Visual Representation Learning": [{"paperId": "moco", "title": "Momentum Contrast for Unsupervised Visual Representation Learning",
                                                                          "authors": [{"name": "Kaiming He"}], "venue": "CVPR", "year": 2020,
                                                                          "abstract": "We present MoCo.", "citationCount": 9000,
                                                                          "journal": None, "publicationDate": "2019-11-13"}],
    "Parameter-Efficient Transfer Learning for NLP": [{"paperId": "houlsby", "title": "Parameter-Efficient Transfer Learning for NLP",
                                                       "authors": [{"name": "Neil Houlsby"}], "venue": "ICML", "year": 2019,
                                                       "abstract": "", "citationCount": 3000, "journal": None,
                                                       "publicationDate": "2019-02-02"}],
}
