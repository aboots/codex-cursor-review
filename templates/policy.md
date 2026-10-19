# Independent review for substantial work

Use an independent reviewer for substantial or difficult implementations and
meaningful experiment-design changes, before declaring that work ready or
launching/submitting its jobs. This is NOT required after every prompt.

Review when a mistake could materially affect correctness or research validity:
new/multi-file algorithms, complex debugging or refactoring, data/split/target
logic, training/evaluation protocols, checkpoint or hyperparameter selection,
metrics/statistics, reproducibility/caching, or nontrivial job orchestration.
Even a small diff deserves review if it changes a consequential experiment rule.

Skip review for explanations, status questions, read-only exploration, typos,
formatting/docs-only changes, and small obvious low-risk edits. Judge substance,
not a fixed line count. Follow an explicit user request to review regardless of
size. For skipped cases, do not launch a reviewer or call Council just to rubber-
stamp the decision. If useful, briefly explain why the change was low risk.

The main agent is __MAIN__; the independent reviewer is __REVIEWER__.
This policy does not apply recursively when you are already the reviewer.

For qualifying work:
1. Implement and run appropriate unit tests/bounded smoke checks. Prepare a local
   request file with requirements, relevant actual diff/new-file content, callers,
   experiment invariants, validation commands and results, known limitations and
   proposed job commands. Exclude credentials and unrelated/private data.
   Cursor ask, Claude Code and Gemini reviewers cannot run shell commands; Codex
   may use read-only file inspection. Provide the actual diff/evidence; never
   outsource tests or jobs to the reviewer. Respect project-specific research
   constraints.
2. If Council MCP is available, inspect the current session before starting one.
   Council 0.4.0 has a single current session; do not replace another active one.
   Council is optional: a full local review is sufficient when it is unavailable.
3. Invoke the separate reviewer:
   `__HELPER__ --reviewer __REVIEWER____MODEL_FLAGS__ --workspace <absolute-path> --prompt-file <request-file>`
   The user can choose a reviewer model with `--model '<exact model ID>'` and, for
   Codex or Claude Code, a reasoning effort with `--effort '<level>'`, or use the per-reviewer
   config in `~/.config/codex-cursor-review/config.json`.
   Do not silently switch reviewer/provider/model on failure. __REVIEWER_NOTE__
4. Read all findings and metadata. Exit 0 means a review returned, NOT approval.
   Assess findings against the actual code/evidence, fix valid problems, document
   disagreements, run relevant checks and re-review substantive fixes. Request
   review at a meaningful completion point, not repeatedly between status prompts.
   Pure cosmetic changes after a clean review do not need another model call.
5. When using Council, relay the external review verbatim with attribution and
   its artifact path; do not misrepresent it as the main agent's own assessment.
   Close your own discussion with the disposition, checks and remaining limits.
6. Report reviewer/model, material fixes, validation and review artifact location.
   Missing/incomplete/failed/BLOCKED reviews are not approval. For qualifying
   work, continue preparation but do not claim readiness or launch/submit its jobs
   until the review is complete and blocking findings are resolved. After three
   unsuccessful fix/review rounds, report unresolved issues rather than loop.

This is an instruction-based workflow, not a shell/Slurm enforcement mechanism.
Review agreement does not prove correctness. A review grants no new permission
to run jobs; existing user-launched-experiment requirements remain in force.
