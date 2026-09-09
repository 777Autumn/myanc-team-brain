# MYANC Team Brain

Agent-maintained Feishu Wiki for a three-person team: meeting preparation,
reviewed knowledge capture, and source-grounded reuse.

Feishu is the published knowledge source. This repository contains implementation,
generic templates, synthetic tests, and deployment documentation only. Private
source material, credentials, pending proposals, and runtime receipts stay outside
Git. Development and CI never deploy the existing bot or CRM.

Implementation contract: [docs/spec.md](docs/spec.md).

Implementation: native Feishu API, host-owned approvals, SQLite journal and three
scoped model tools. Runtime uses Python 3.11+ with no third-party dependencies.

```sh
python -m unittest discover -s tests -v
```

- [Eight review drafts](drafts/README.md)
- [Tool protocol](docs/tools.md) and [bot skill](skills/team-brain/SKILL.md)
- [Release checklist and recovery](docs/release-checklist.md)

No live tenant has been configured or deployed by this implementation. Production
activation and the three-person real workflow acceptance remain separate steps.
