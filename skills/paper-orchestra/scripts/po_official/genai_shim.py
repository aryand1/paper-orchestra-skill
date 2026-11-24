# Copyright 2026 aryand1 and PaperOrchestra skill-pack contributors.
# Licensed under the Apache License, Version 2.0 (see LICENSE-APACHE-2.0).
#
# Minimal stand-in for the parts of `google.genai.types` that the official
# google-research/paper-orchestra code uses. It only records what each part
# holds so the call layer (llm.py) can write it to a prompt file for the host
# agent. Nothing here talks to a network or a model.

from types import SimpleNamespace


class Part:
    def __init__(self, text=None, data=None, mime_type=None):
        self.text = text
        self.data = data
        self.mime_type = mime_type

    @staticmethod
    def from_text(text):
        return Part(text=text)

    @staticmethod
    def from_bytes(data, mime_type):
        return Part(data=data, mime_type=mime_type)


class GoogleSearch:
    """Marker: the official call used Gemini's Google Search grounding."""


class Tool:
    def __init__(self, google_search=None):
        self.google_search = google_search


def GenerateContentConfig(**kwargs):  # noqa: N802 (mirror official name)
    return dict(kwargs)


types = SimpleNamespace(
    Part=Part,
    Tool=Tool,
    GoogleSearch=GoogleSearch,
    GenerateContentConfig=GenerateContentConfig,
)
