# Development contract

- GitHub delivery and VPS candidates follow the shared authority in
  `77AutumN/myanc-crm:ops/RELEASE.md`. A knowledge-runtime candidate requires
  the optional third SHA there; first-install/bootstrap scripts are not upgrades.

- Keep the implementation in this repository. Existing CRM and gateway working
  trees contain unrelated work; gateway integration belongs in a separate checkout.
- `docs/spec.md` is the implementation scope. Changing production services,
  credentials, permissions, or restarting the bot requires separate user approval.
- Use Python's standard library for the host and SQLite journal. Run
  `python -m unittest discover -s tests -v` and `git diff --check` before a code commit.
- Model tools receive business arguments only. Identity comes from the gateway;
  publication requires a persisted, card-bound decision from its trusted callback.
- Never commit customer sources, personal knowledge, live identifiers, credentials,
  pending proposals, or receipts. Fixtures use clearly fictional data.
- Review the spec and standards independently before merging. CI must pass on the
  reviewed commit; CI has no production credentials or deployment step.
