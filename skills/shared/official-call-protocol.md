# Official mode: how to answer a pending LLM call

In official mode, `po.py advance` runs Google's pipeline code. Wherever that
code would call Gemini, it writes the exact model input to a **prompt file**
and stops. You (the host agent) play the model for that one call and write the
reply to the **response file**. Then you run `advance` again and the pipeline
continues from where it stopped.

`advance` prints JSON like this:

```json
{"status": "pending", "count": 4, "calls": [
  {"id": "a1/lit/discovery/task000/c01.r1",
   "prompt_file": "workspace/.po/calls/a1/lit/discovery/task000/c01.r1.prompt.md",
   "response_file": "workspace/.po/calls/a1/lit/discovery/task000/c01.r1.response.md",
   "tools": ["web_search"], "attachments": [], "official_model": "gemini-3-flash-preview"}]}
```

## Rules for every call

1. **One call, one fresh context.** Answer each call as if it were a separate
   API request. Do not let earlier calls, the repo, or this conversation shape
   the answer. In Claude Code, give each call to its own sub-agent (Agent tool).
   This matters most for the reviewer calls (`/review_*/`), which must judge the
   paper with no knowledge of how it was written.
2. **Use only what the prompt file gives you.** Treat `SYSTEM INSTRUCTION` as
   the system prompt and the `USER CONTENT` parts, in order, as the user turn.
   Open each attachment (figure, page grid, PDF) with the Read tool.
3. **Tools.** Use web search only when the call's `tools` list has
   `web_search` (the literature discovery calls; the official code used
   Gemini's Google Search grounding there). No other tools.
4. **Reply exactly as the model would.** Follow the output format the prompt
   asks for (for example a ```json block, a ```latex block, or plain text).
   Write the whole reply, unedited, to `response_file`. Do not add notes.
5. **Never edit prompt files, earlier responses, or pipeline outputs by hand.**
   If a reply cannot be parsed, the pipeline issues a retry call (`.r2`, `.r3`,
   ... up to 5, as in the official code). If a step fails, the pipeline retries
   the whole run up to 3 times, as the official code does.
6. Answer all pending calls (they are independent; run them in parallel),
   then run `advance` again. Repeat until `status` is `done`, `failed` or
   `error`.

## Claude Code: sub-agent prompt

Spawn one `general-purpose` sub-agent per pending call, up to about 10 at a
time, with this prompt (fill in the two paths and the tool line):

```
You are acting as a language model for exactly one API call.
1. Read the prompt file: <prompt_file>
2. Follow its "Executor instructions". Use the SYSTEM INSTRUCTION as your
   system prompt and the USER CONTENT parts, in order, as the user message.
   Open every attachment it lists with the Read tool.
3. <Either: "You may use WebSearch to find real papers for this call."
    Or:     "Do not use any tool except Read for the listed attachments.">
4. Do not read any other files and do not use any other context.
5. Write your complete reply, exactly as the model would return it and in the
   output format the prompt requests, to: <response_file>
   Use the Write tool. Then answer "done".
```

## Other hosts (Cursor, Cline, Aider, ...)

If your host has no sub-agents, answer each call in a new chat or task when
you can. If you must answer in the same context, answer each call only from
its prompt file and keep reviewer calls free of anything you know about the
draft. Results will be less faithful to the official pipeline.

## What you cannot match (and should not try to)

- **Model and sampling.** The official code uses `gemini-3.1-pro-preview`
  (and `gemini-3-flash-preview` for discovery) with set temperatures. Each
  prompt file lists them; your host cannot set them. Just answer normally.
- **Diagrams.** The official code draws diagrams with an image model. Here the
  diagram call asks for matplotlib code instead (the prompt file marks this
  with a `[HOST ADAPTER]` note). Statistical plots are code in both.
