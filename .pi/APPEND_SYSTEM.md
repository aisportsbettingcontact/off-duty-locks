Execution rules for this repo (appended to every pi session):

- Skills: if any entry in <available_skills> plausibly applies to the task — even 1% —
  read and follow it before acting. Process skills (brainstorming, systematic-debugging,
  test-driven-development, verification-before-completion) come before domain skills.
- Models: current-generation only per LLM.md (claude-fable-5 default, claude-opus-5,
  gpt-5.6-sol). Never switch to older models.
- Verification before claiming done: `python -m pytest -q` and
  `python3 qa/verify.py --repo-root .` must pass; report real command output, never
  assumed success.
- LKG law: failed runs never touch last-known-good. Never hand-edit `data/`;
  quarantine is append-only evidence.
- Honest gates: a BLOCKED gate is never reported as PASS (qa/acceptance-gates.md).
- Compliance: never add retry aggression, header spoofing, or bot evasion. Live-source
  requests are budgeted (docs/compliance.md); the Live Smoke workflow is the only
  sanctioned live path — never probe *.wnba.com ad hoc.
- Shipping: merge to main deploys the Railway web service. Use the /ship template;
  it encodes the gates.
