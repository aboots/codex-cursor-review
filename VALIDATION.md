# Validation

Pre-publication validation on 2026-10-11:

- **39 offline tests passed** on Linux x64 / Python 3.14. These cover both CLI
  adapters, verdict/error handling, process timeouts and races, Unicode paths,
  installer idempotency, CLI dry-run/home isolation, config preservation, malformed
  TOML/JSON, legacy-policy conflicts, pinned downloads and archive validation.
- **Fresh installation passed:** temporary home/project; both main-agent policies;
  installed executable starts; real Council download matches the repository pin
  and registry metadata; executable reports 0.4.0; both MCP configs parse.
- **Cursor reviewer passed:** three real package review rounds requested
  `claude-opus-5-5-medium`. First round identified installer/TOML/integrity issues;
  those were fixed and covered by tests. Final round returned
  `READY FOR MAIN AGENT VERIFICATION` with no blocking findings.
- **Codex adapter connection passed** for a prompt-contained code sample; the
  final-message file was captured and its verdict parsed.
- **Codex direct file-inspection smoke was BLOCKED on this host**:
  `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`. This environment
  cannot run that Codex read-only sandbox operation. No bypass was added. Do not
  call the reverse direction fully verified on this host; use a sandbox-capable
  host and validate it there. The default Codex-main/Cursor-reviewer path works.
- **Editor/MCP:** Council initialize/tools-list and Cursor CLI readiness verified
  on the original host; its user confirmed Cursor editor connected. Other
  servers still need their own editor connection check.
- **CI** is configured for Ubuntu/macOS and Python 3.11/3.13; see GitHub Actions
  for actual results after publication. macOS model calls and non-x64 Council
  binaries have not been live-tested here. Their exact published package hashes
  are recorded, and each install must match them.

Raw review transcripts, account data and private machine paths stay outside Git.
Model identifiers/effort are requested CLI settings, not independent attestation
of provider routing. Reviewers inspect supplied evidence/code but do not execute
validation tests; the main agent supplies those results. Ask/read-only modes and
prompts are not an MCP security boundary. The review trigger is an instruction-
based judgment about substantial work, not a forced hook on every prompt.
