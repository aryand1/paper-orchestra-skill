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
# MODIFIED (2026, PaperOrchestra skill pack), from methods/paper_writer.py
# at google-research/paper-orchestra@d9bae99. Changes: the 3-try loop resumes
# from a saved attempt number (`start_try`, `on_try_failed`) because a run is
# spread over several `po.py advance` invocations; each stage runs in a named
# llm.scope; the batch-benchmark helpers were removed. The stage order,
# file layout, copies and agent settings are unchanged.

import os
import os.path as osp
import shutil
import traceback
import json

from po_official.outline_agent import OutlineAgent
from po_official.literature_review_agent import HybridLiteratureAgent
from po_official.section_writing_agent import SectionWritingAgent
from po_official.content_refinement_agent import ContentRefinementAgent
from po_official import llm

from po_official.common_utils import (
    create_log_folder,
)


def write_single_paper(
    base_dir,
    latex_template_dir,
    idea_filename="idea_dense.md",
    experimental_log_filename="experimental_log.md",
    writer_model_name: str = "gemini-3.1-pro-preview",
    reflection_model_name: str = "gemini-3.1-pro-preview",
    research_cutoff: str = "2024-11",
    start_try: int = 0,
    on_try_failed=None,
):
    latex_writeup_dir = osp.join(base_dir, "latex_writeup")
    pdf_file = osp.join(base_dir, "final_paper.pdf")

    if osp.exists(latex_writeup_dir):
        shutil.rmtree(latex_writeup_dir)
    if osp.exists(pdf_file):
        os.remove(pdf_file)

    materials_dir = osp.join(base_dir, "raw_materials")

    old_figure_dir = osp.join(materials_dir, "figures")
    new_figure_dir = osp.join(latex_writeup_dir, "figures")
    if osp.exists(old_figure_dir):
        shutil.copytree(old_figure_dir, new_figure_dir, dirs_exist_ok=True)
    figures_dir = new_figure_dir

    max_n_tries = 3
    cur_try = start_try
    succeed = False

    while (not succeed) and (cur_try < max_n_tries):
        llm.configure(llm._CTX["calls_dir"], attempt=cur_try + 1)
        try:
            assert osp.exists(
                materials_dir
            ), f"Please make sure '{materials_dir}' exists!"

            shutil.copytree(latex_template_dir, latex_writeup_dir, dirs_exist_ok=True)

            guidelines_path = osp.join(latex_writeup_dir, "guidelines.md")
            latex_template_path = osp.join(latex_writeup_dir, "template.tex")

            idea_file_path = osp.join(materials_dir, idea_filename)
            experimental_log_file_path = osp.join(
                materials_dir, experimental_log_filename
            )

            assert osp.exists(
                guidelines_path
            ), f"No guidelines file detected at '{guidelines_path}'!"
            assert osp.exists(
                latex_template_path
            ), f"No template file detected at '{latex_template_path}'!"
            assert osp.exists(
                idea_file_path
            ), f"No idea file detected at '{idea_file_path}'!"
            assert osp.exists(
                experimental_log_file_path
            ), f"No experimental log file detected at '{experimental_log_file_path}'!"

            ###############################################################################################
            # Step 1: Call outline agent
            outline_agent = OutlineAgent(
                model_name=writer_model_name, cutoff_date=research_cutoff
            )
            outline_file_path = osp.join(base_dir, "outline.json")
            with llm.scope("outline"):
                outline_agent.run(
                    idea_file=idea_file_path,
                    experimental_log_file=experimental_log_file_path,
                    latex_template_file=latex_template_path,
                    conference_guidelines_file=guidelines_path,
                    output_filepath=outline_file_path,
                )

            ###############################################################################################
            # Step 2: Call HybridLiteratureAgent
            literature_agent_output_dir = osp.join(base_dir, "literature_agent_output")
            literature_agent = HybridLiteratureAgent(
                idea_path=idea_file_path,
                experimental_log_path=experimental_log_file_path,
                latex_template_path=latex_template_path,
                conference_guidelines_path=guidelines_path,
                output_dir=literature_agent_output_dir,
                model_name=writer_model_name,
                max_workers=3,
            )
            with llm.scope("lit"):
                literature_agent.run(
                    outline_path=outline_file_path,
                    cutoff_date=research_cutoff,
                )
            shutil.copy(
                osp.join(literature_agent_output_dir, "outline_v1.json"),
                outline_file_path,
            )
            shutil.copy(
                osp.join(literature_agent_output_dir, "updated_template.tex"),
                osp.join(latex_writeup_dir, "template.tex"),
            )
            shutil.copy(
                osp.join(literature_agent_output_dir, "references.bib"),
                osp.join(latex_writeup_dir, "references.bib"),
            )

            ###############################################################################################
            # Step 3. Call SectionWritingAgent
            citation_map_path = osp.join(
                literature_agent_output_dir, "citation_map.json"
            )
            section_writing_agent = SectionWritingAgent(
                outline_path=outline_file_path,
                template_path=osp.join(latex_writeup_dir, "template.tex"),
                idea_path=idea_file_path,
                experimental_log_path=experimental_log_file_path,
                citation_map_path=citation_map_path,
                figures_info_path=osp.join(figures_dir, "info.json"),
                guidelines_path=guidelines_path,
                model_name=writer_model_name,
            )
            raw_draft_tex_path = osp.join(latex_writeup_dir, "raw_draft_paper.tex")
            with llm.scope("section"):
                section_writing_agent.run(output_path=raw_draft_tex_path)

            ###############################################################################################
            # Step 4: Content Reflection
            content_refinement_workdir = os.path.join(
                base_dir, "content_refinement_workdir"
            )
            if os.path.exists(content_refinement_workdir):
                shutil.rmtree(content_refinement_workdir)
            os.makedirs(content_refinement_workdir, exist_ok=True)

            shutil.copytree(
                latex_writeup_dir,
                content_refinement_workdir,
                dirs_exist_ok=True,
            )

            content_refinement_agent = ContentRefinementAgent(
                experimental_log_path=experimental_log_file_path,
                citation_map_path=citation_map_path,
                guidelines_path=guidelines_path,
                model_name=reflection_model_name,
                max_reflections=3,
                work_dir=content_refinement_workdir,
            )
            with llm.scope("refine"):
                content_refinment_agent_output_pdf_path = content_refinement_agent.run(
                    texfile_path=raw_draft_tex_path
                )
            assert osp.exists(
                content_refinment_agent_output_pdf_path
            ), f"Content Refinement Agent failed to produce final PDF at {content_refinment_agent_output_pdf_path}!"

            final_pdf_path = osp.join(base_dir, "final_paper.pdf")
            print(
                f" >> Copying final PDF from  {content_refinment_agent_output_pdf_path} to {final_pdf_path}...\n"
            )
            shutil.copy(content_refinment_agent_output_pdf_path, final_pdf_path)
            print(" >> Final PDF saved to:\n", final_pdf_path)

            succeed = True
            return True

        except Exception:
            print(f"[Try {cur_try+1}/{max_n_tries}] EXCEPTION in perform_writeup:")
            print(traceback.format_exc())
            if on_try_failed is not None:
                on_try_failed(cur_try + 1, traceback.format_exc())
            cur_try += 1
            if cur_try < max_n_tries:
                print("Try again now...")

    print(f"Writeup failed after {max_n_tries}! Stop here.")
    return False
