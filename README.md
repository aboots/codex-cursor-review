# Codex ↔ Cursor Review

Let one agent implement substantial work and a separate agent review it before
you trust the result or run an experiment. Keep working in your editor: the
reviewer runs through its CLI in the background, using your own account.

**Default:** Codex implements → Cursor / **Opus 5.5 medium** reviews → Codex
checks the findings, fixes valid issues, tests, and requests a follow-up review.
The reverse direction—Cursor implements, Codex reviews—is also supported.
**Claude Code** implements → Codex / **GPT-6 Astra medium** (`gpt-6-astra`) reviews.

**Not a review after every prompt.** The installed policy requests reviews for
substantial, difficult, or consequential code and experiment changes. Explanations,
status questions, typos, formatting, and small obvious low-risk edits skip review.
A tiny change to test-data handling or checkpoint selection can still be consequential.

This is an instruction-driven workflow, not a command interceptor or Slurm lock.
The main agent must follow the policy and assess the review. Agreement between
models is useful evidence, not a guarantee of correctness.

## Quick start on another server

Requires Linux, macOS, or WSL, **Python 3.11+**, Git, and authenticated agent CLIs.
No Python packages, API keys in this repository, or paid Council service are needed.
Model calls use your existing agent accounts and their applicable usage limits.

1. Install/authenticate the agents on **the machine running your editor's remote
   workspace**, not only on your laptop. Follow the official
   [Codex CLI instructions](https://developers.openai.com/codex/cli/) and
   [Cursor CLI instructions](https://cursor.com/docs/cli/overview).

   ```bash
   codex login
   cursor-agent login
   cursor-agent models
   ```

   If a CLI is not on PATH, configure its absolute executable path below. A Codex
   extension's bundled CLI may work, but a standalone installation is easier to
   keep on PATH across extension updates. Login credentials are never copied here.

2. Clone and install:

   ```bash
   git clone https://github.com/aboots/codex-cursor-review.git
   cd codex-cursor-review
   python3 install.py --main codex --with-council
   ~/.local/bin/codex-cursor-review --reviewer cursor --check
   ```

   `--with-council` installs the pinned **MrLesk Agents Council 0.4.0** native
   binary, verifies its SHA512 against `council-integrity.json` and npm metadata, and registers its MCP in both
   Codex and Cursor. Node/npm is not needed for that step. It downloads only
   Council; agent CLIs and authentication remain separate.

   Council is optional. Omit `--with-council` for a local artifact-only workflow.
   Omitting the flag does not remove an existing Council registration.

3. Reload your editor and start a new conversation. For remote workspaces,
   confirm `council` is connected in the editor's MCP settings. Tell Codex:

   > Implement this experiment. Follow the independent-review policy when the
   > implementation or experimental design warrants it.

On subsequent substantial tasks, the persistent policy tells the main agent to
prepare the review request, launch the reviewer, address findings and report the
outcome. You do **not** need to invoke the CLI after every prompt yourself.

To preview changes without downloads or writes:

```bash
python3 install.py --main codex --with-council --dry-run
```

## Choose the main agent

| Main agent | Reviewer | Installation |
|---|---|---|
| Codex (default) | Cursor, Opus 5.5 medium | `python3 install.py --main codex --with-council` |
| Cursor | Codex, configured Codex model with medium effort | `python3 install.py --main cursor --project /path/to/project --with-council` |
| Claude Code | Codex, GPT-6 Astra medium | `python3 install.py --main claude --with-council` |
| Codex and Cursor | The other agent | `python3 install.py --main both --project /path/to/project --with-council` |
| Codex, Cursor, and Claude Code | The other agent; Claude Code uses Astra medium | `python3 install.py --main all --project /path/to/project --with-council` |

Codex instructions are installed in `$CODEX_HOME/AGENTS.md` (normally
`~/.codex/AGENTS.md`), shared by its CLI and extension on that host. Cursor
main-agent rules are **project-scoped** in
`<project>/.cursor/rules/codex-cursor-review.mdc`; rerun with `--project` for each
project where Cursor should implement and Codex should review. The rule includes
a local executable path; avoid committing that server-specific generated file.
Claude Code instructions are installed in `~/.claude/CLAUDE.md`, shared by the
Claude Code CLI and extension on that host. Start a new Claude Code conversation
after installing.

`--main cursor` and `--main claude` do not delete an already-installed Codex
policy. Each policy passes the reviewer explicitly, so installing more than one
cannot make an agent review itself accidentally. A session already acting as
reviewer must never launch another reviewer recursively. The Claude Code command
pins `--model gpt-6-astra --effort medium`. That pin is the installed default for
Claude Code; it does not change the Cursor → Codex path, which still uses the
account's configured Codex model unless you pass `--model`.

## When reviews happen

Review substantial or difficult implementations, complex refactors/debugging,
and changes that materially affect an experiment: splits, targets, preprocessing,
training/evaluation, checkpoint/tuning selection, statistical claims, reproducible
caches, baseline fairness, or nontrivial job orchestration. Review a coherent batch
at a meaningful completion point, not every small intermediate edit/message.

Skip explanations, progress/status requests, read-only exploration, documentation
wording, formatting, typos, and small obvious low-risk edits. An explicit user
request for review always takes precedence. A small diff with large scientific
consequences is not low risk. Cosmetic follow-ups after a clean review need not
trigger another model call. This judgment is made by the main agent, not by a
brittle line-count heuristic in the helper. Manually invoking the helper always
runs a review.

## Select the reviewer model

Edit `~/.config/codex-cursor-review/config.json`:

```json
{
  "default_reviewer": "cursor",
  "timeout_seconds": 900,
  "reviewers": {
    "cursor": {"model": "claude-opus-5-5-medium"},
    "codex": {"model": null, "effort": "medium"}
  }
}
```

`null` for Codex means use that account's configured Codex model. Cursor's default
is the exact Opus 5.5 medium identifier verified on the original installation;
availability can differ by account and over time. Run `cursor-agent models` on
each server. No automatic fallback to another provider/model is implemented.
Tell your main agent “use MODEL as reviewer for this task” for a one-off override.

Claude Code's installed policy does not use that null default. Its command is
`--reviewer codex --model gpt-6-astra --effort medium` (GPT-6 Astra, medium
reasoning). Codex CLI 0.153.0 or newer is required for Astra. A one-off
`--model` or `--effort` still overrides that invocation when you ask for it.

For CLIs outside PATH, add `"binary": "/absolute/path/to/cursor-agent"` or
`"binary": "/absolute/path/to/codex"` to that reviewer's options. Use a CLI
executable, not the Cursor desktop executable. The installer preserves configured
reviewer models when rerun; `--main` sets the manual CLI default reviewer.

## Manual review / troubleshooting

Prepare a request using [the template](examples/review-request.md). Include the
requirements, actual relevant diff or new code, important callers/invariants,
and real validation results. Cursor ask mode cannot run shell commands; Codex may use read-only inspection
commands. Supply the actual relevant diff and never ask the reviewer to run tests
or jobs. Keep credentials and unrelated
private data out of the request; the selected provider receives this context.

```bash
~/.local/bin/codex-cursor-review \
  --reviewer cursor \
  --workspace /path/to/project \
  --prompt-file /path/to/request.md

# Reverse direction, optional model override:
~/.local/bin/codex-cursor-review \
  --reviewer codex \
  --workspace /path/to/project \
  --prompt-file /path/to/request.md \
  --model YOUR_CODEX_MODEL

# Claude Code's installed reviewer: Codex GPT-6 Astra at medium effort.
~/.local/bin/codex-cursor-review \
  --reviewer codex \
  --model gpt-6-astra \
  --effort medium \
  --workspace /path/to/project \
  --prompt-file /path/to/request.md
```

Cursor runs with `--mode ask`; Codex runs with `--sandbox read-only` and approvals
set to never. No `--force`, `--yolo`, or sandbox bypass is used. The prompt allows read-only file inspection but forbids
edits, tests/builds, experiments, jobs, network commands, recursive agents and MCP calls. Existing agent config,
plugins and MCP servers may still be loaded: this is **not** a security boundary
against all external actions. Use a dedicated account/environment if you need one.

Each call writes a private run directory under
`~/.local/share/codex-cursor-review/reviews/` with:

- `request.md`: exact submitted prompt;
- `review.md`: reviewer answer (Codex uses its final-message file);
- `metadata.json`: requested model, command, request hash, timestamps/status/verdict;
- `stderr.txt`, plus `stdout.txt` for Codex progress.

No reviews are uploaded to GitHub. When Council is available, the main agent
relays the review there with attribution; the reviewer does not need to call
Council itself. Council 0.4.0 has **one current session**: never overwrite another
active discussion. Council cannot automatically summon Cursor; the helper provides
that bridge. Local artifacts remain sufficient if Council is unavailable/busy.

**Exit 0 means a completed review, not approved code.** It includes both
`READY FOR MAIN AGENT VERIFICATION` and `CHANGES REQUIRED`. Read the verdict and
all findings. Missing/empty/truncated output, failed processes and an explicit
`BLOCKED` fail closed. Timeouts terminate the reviewer process group and return
124. The main agent must fix valid issues, test and re-review material changes.
After three unsuccessful rounds, report unresolved issues instead of looping.

Requested model/effort are recorded; CLI output does not independently attest
provider routing. Do not infer actual model identity solely from its self-report.
A passing review does not authorize an experiment or submission beyond what the
user already authorized. Existing “full runs are user-launched” rules still apply.

### Codex sandbox limitations on restricted servers

The reverse Cursor → Codex direction needs a working Codex read-only sandbox.
On some restricted Linux containers/HPC hosts, file inspection can fail with
`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`. That happened
in the original host's direct-inspection smoke test. The adapter returns BLOCKED;
it does not disable the sandbox or silently treat supplied context as a passed
inspection. Use a host that supports Codex's sandbox, or use the tested default
Codex → Cursor workflow. A prompt-only connection check cannot prove file access.

## Installer behaviour, updates, and migration

The standard-library installer copies the helper into `~/.local/bin`, adds a
marked policy block, merges MCP registrations, and makes timestamped backups of
changed existing text files. It preserves unrelated instructions/configuration.
A conflicting Council registration, unsupported TOML layout, malformed policy markers, or target symlink
causes it to stop rather than replace those settings. If `AGENTS.override.md` exists, the installer warns that it takes precedence;
merge the review policy into that file to activate it. Use an installer Python
with a short executable path without whitespace (kernel shebang constraint).
Additional fields on an existing Council entry require manual merging; the
installer conservatively refuses to alter them. Installs use per-file atomic
writes, not an all-files transaction; after a write failure, inspect backups and
rerun. Dry-run lists planned destinations, some of which may be unchanged.
The installer does not
change your shell PATH, log you in, or copy credentials. Use the absolute command
or add `~/.local/bin` to PATH yourself.

Update with `git pull --ff-only`, then rerun the same install command. The helper
is copied, so moving/deleting the clone does not break it. It uses the Python
interpreter that ran the installer; reinstall after removing that interpreter.
For another Codex profile, pass `--codex-home /path/to/profile`. `CODEX_HOME` is
honoured for normal installation. `--home /tmp/review-install` provides a separate
test/preview installation root and defaults Codex config under that root.

If upgrading from the earlier local-only prototype, back up the old
`~/.codex/AGENTS.md` and remove **only** its unmarked section titled
`# Independent Cursor review required`. That policy reviewed every code change;
the new marked block uses the selective policy above. The installer refuses to
leave both conflicting policies silently active. Legacy `model` and
`timeout_seconds` JSON settings migrate without changing the chosen model.
Old review archives stay local. Do not copy them into this repository.

To uninstall, remove the marked `codex-cursor-review:begin/end` policy block, the
generated Cursor rule, and the same marked block in `~/.claude/CLAUDE.md`, then
remove `~/.local/bin/codex-cursor-review`. If you
also want to remove Council, remove only its server entry from the Codex TOML,
Cursor MCP JSON, and `~/.claude.json` (or use `codex mcp remove council` for Codex).
Preserve other MCP servers and your review archives. Backups can help recover
earlier settings; don't blindly restore them over newer unrelated changes.

## Validation and scope

```bash
python3 -m unittest discover -s tests -v
```

Offline tests exercise both CLI adapters using fake processes, verdict/error
handling, timeouts, Unicode prompts, installer idempotency, config preservation,
legacy-policy conflicts and isolated installations. They do not call models or
require credentials. CI runs these checks on Linux/macOS and Python 3.11/3.13.
A CI test is not evidence that your particular account/model is available.

Live reviewer testing and residual limitations are recorded in
[VALIDATION.md](VALIDATION.md). Native Windows is not supported; use WSL. Install
on every remote server where the workflow should run. A separate local editor
workspace has its own configuration; the installer cannot change your laptop
from a remote shell.

## Why this combination?

[MrLesk's Agents Council](https://github.com/mrlesk/agents-council) supplies shared
MCP discussions, but its summon tool supports Claude Code/Codex, not Cursor. This
small helper supplies the separate Cursor process, the pinned Codex GPT-6 Astra
review used by Claude Code, and persisted review results. `--with-council` also
registers Council for Claude Code in `~/.claude.json` when `--main claude` or
`--main all` is selected. Other install modes leave that file unchanged.

[Andrew Vaughan's Agent Council](https://github.com/andrewvaughan/agent-council)
is a collection of persona/workflow skills, and
[Karpathy's LLM Council](https://github.com/karpathy/llm-council) is an OpenRouter
multi-model chat app. Other options considered included
[ktaletsk/council](https://github.com/ktaletsk/council),
[kiran-agentic/agentcouncil](https://github.com/kiran-agentic/agentcouncil), and
[cursor-delegate-mcp](https://github.com/andreilungeanu/cursor-delegate-mcp).
This package targets the narrower experiment-review workflow; it is not a claim
of superior model accuracy or a comparative reliability benchmark.

Independent project; not affiliated with OpenAI, Cursor, Anthropic or Agents
Council. The repository contains original integration code only; Council remains
a separately downloaded dependency with its own license. See [LICENSE](LICENSE).
