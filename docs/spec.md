# Team Brain v1 implementation contract

## Product

Use the existing Feishu bot to prepare customer meetings, capture submitted text,
Feishu documents or existing transcripts, preview proposed Wiki changes, publish
after the submitting member confirms, and reuse the published knowledge next time.
Three members share the same explicitly configured team knowledge space while the
existing six conversation environments remain isolated. Banquet business is the
initial topic; generic FDE methods remain reusable.

Feishu Wiki is canonical. The private GitHub repository contains code, generic
templates, synthetic fixtures and release records, not actual team knowledge.
Personal FDE knowledge enters only as individually selected and owner-reviewed
team versions. Never scan or synchronize the personal workspace or chat history.

## Knowledge lifecycle

- Separate partner report, hypothesis, customer confirmation and verified outcome.
  Approval confirms the record, not the customer's acceptance or verified truth.
- Eight seed drafts: team/service boundaries; banquet vocabulary/process; discovery
  interview; meeting brief; meeting facts/questions; evidence/system inventory;
  narrow workflow selection; solution/acceptance draft.
- Model surface: catalog, search, read, propose. Search/read return source IDs, URLs and
  current evidence state; proposals carry explicit source excerpts and references.
- A proposed update includes the prior content and readable diff. Only its proposer
  in the bound conversation can confirm the exact persisted preview/card.
- Content changes require confirmation. Index/related-page maintenance may run
  automatically following verified publication; it must not rewrite factual text.
- Drafts are not in the published search corpus. Unknown or conflicting evidence
  remains explicit. Distinct customer subjects must not be silently merged.

## Host boundaries

Use Feishu's native document search with a host-fixed Wiki space filter, preserving
its ranking and opaque pagination. Search accepts a nonblank query of at most 30
characters and checks one five-hit page per call. Filter hits against the published
registry, customer subject, root ancestry and publication locks before returning
current node titles and canonical links. Search does not download bodies or grant
a source-read receipt; knowledge_read remains required before citation/proposal.
Do not introduce a local search index or literal-matching fallback. Native API
identity support, body matching and index freshness require live acceptance.

For general methods, use knowledge_catalog over the existing published registry
before reading relevant pages. It accepts only subject and optional page_token;
each call checks at most five same-subject/team candidates using stable node-key
pagination, the current root/space and locks before and after remote lookup.
Paused publishing does not block reads. Cursors bind identity/session/subject and
are not snapshots; even a filtered empty page can have continuation. Directory
metadata grants no source-read receipt. Permission, API and database failures must
remain failures, not empty results. No body cache, new table, title allowlist,
vector index or general space enumeration is introduced. The skill's tool reference
is the authority for choosing catalog vs native search and handling follow-ups.

The trusted gateway fixes platform, user, chat, session and project. The host
validates that tuple against configured environments and fixes the Wiki space,
root and API origin. Model arguments cannot select identities, credentials,
spaces, API endpoints, filesystem paths or approve/execute operations.

Use a separate SQLite journal for proposals, card binding, claims, publication
steps and receipts. Do not share a CRM ledger. Serialize publication per page;
re-read before update, reject an obsolete preview, change only selected text
blocks and verify by reading back. Pause a page before administrator manual edits.
Feishu revisions are not strict CAS locks.

Track uncertain writes durably. Reconcile a timed-out creation or block write
before any retry; do not automatically recreate a node or replay an unknown write.
Reuse a stable client_token where the API supports it. A process crash must not
convert an executing action into a new approval or clear its unresolved page lock.

Use Feishu native API endpoints, not CLI shortcuts that automatically grant extra
permissions. No deletion, permission changes, cross-space movement, automatic
recording/transcription or whole-chat collection in v1.

## Delivery

Implement and test locally, open a private-repository PR with CI, and prepare a
separate gateway integration PR. Existing production/local/GitHub gateway versions
must be reconciled before activation; do not fold unrelated CRM changes into a PR.
CI runs tests only. Credentials, scopes, resource access, installation and restart
are listed in a reviewable release checklist and require separate confirmation.

## Acceptance

Synthetic integration tests cover a meeting-to-capture-to-publish-to-read journey,
source grounding, missing evidence, separate customers, the distinction between
system-user and employee counts and between Excel export and an API, private-source
exclusion, unapproved/duplicate/expired approvals, stale previews, concurrent writes,
timeouts/partial writes, restart reconciliation, and gateway identity isolation.
Live tenant capability and the three-person real workflow remain release acceptance
steps, never inferred from mock tests or a successful build.
