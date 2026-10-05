# Team Brain v1 implementation contract

## Product

Use the existing Feishu bot to prepare customer meetings, capture submitted text,
Feishu documents or existing transcripts, preview proposed Wiki changes, publish
after the submitting member confirms, and reuse the published knowledge next time.
Two to five members share the explicitly configured team knowledge space while
their four to ten conversation environments remain isolated (exactly two per member).
The protected gateway mapping identifies private and group entrances; names do not grant access.
Synthetic tests cover this capacity; current live acceptance uses two real accounts.
Banquet business is the
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

The protected host configuration may enable `subject_access`: a complete list of
`{"principal": <trusted transport identity>, "subjects": ["team", ...]}` entries,
one per configured conversation principal. Subject names alone never grant access.
An enabled policy rejects unknown subjects before search, metadata, body reads or
proposal creation. Status, binding, approval and execution recheck the stored
proposal subject against the current policy. A manager's private and group
principals are distinct grants. Missing/duplicate entries and null policies fail
closed; omitting the field retains the legacy shared-knowledge contract and must
not be described as confidential-subject protection.

`team` remains explicitly shared with all configured principals. Restricted pages
must be registered under their restricted subject, never `team`. Exact IDs,
references, search results and proposal sources still pass the existing registry
subject checks. A two-account fixture grants `management` only to the manager's
private principal, and `team` to all four principals. This is a host policy for the
existing service identity, not proof of end-user Feishu ACL inheritance. Direct
Feishu access must remain separately scoped. Already delivered text, files and
conversation history cannot be recalled by this policy; permission revocation
requires handling those existing artifacts before reusing a session.
The existing publisher creates pages under a shared Wiki root, so proposals for
subjects not granted to every configured principal are blocked. Read authorization
does not establish private native publication permissions. This patch does not
enable confidential publishing; shared-subject publication keeps its existing
approval lifecycle.

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

Answer completion and conditional source display follow
[the team-brain Skill](../skills/team-brain/SKILL.md). The September 11 Owner decision
removes a mandatory-link presentation gate for ordinary grounded answers; it does
not remove source reads, scoped read receipts or evidence/privacy boundaries.
Existing conversation/tool records support bounded operator audits; this change
does not add a permanent answer-to-source database or a model-visible audit tool.

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

### Proposal status follow-up repair

After cancellation, a model must not infer pending approval from an unchanged
page. Add knowledge_status(approval_id), a read-only lookup using the existing
journal and authenticated Principal (platform/user/chat/session-key/project).
It returns an observed state, fixed-language summary and limited verified receipt;
it never calls Feishu, regenerates a card, changes approval state or exposes draft
content. Pending expiry is projected without a journal mutation; executing/unknown
remain uncertain. Model session-token rotation does not change the bound Principal.
Other principals and extra identity/action fields are rejected. Legacy proposals
remain readable without migration. Status questions require a fresh status read;
current-page questions separately keep the existing reread requirement. This
provides the missing evidence channel, not proof of live model compliance: replay
the Test cancellation question after a separately authorized release.

Implement and test locally, open a private-repository PR with CI, and prepare a
separate gateway integration PR. Existing production/local/GitHub gateway versions
must be reconciled before activation; do not fold unrelated CRM changes into a PR.
CI runs tests only. Credentials, scopes, resource access, installation and restart
are listed in a reviewable release checklist and require separate confirmation.

## Acceptance

The September 12 review UX increment adds optional `notes` (suggestion/question)
with the same source, quote, privacy and subject checks; `statements` remains
required, and both arrays contain 1–12 items combined. Notes never acquire a
factual evidence category. Equal statement/quote text appears once with its source;
inferences and notes retain their basis. Only new proposals use this rendering.

The host additionally freezes structured `review` fields (operation, title,
destination, before, after), while retaining preview/diff for older gateways.
Existing proposals execute their original frozen content without migration.
The gateway displays complete wrapping literal text, safe host-generated links and
buttons, and checks the final serialized approval against 24,000 bytes before
binding/delivery. Create displays the proposed body; append displays existing and
added content; replace displays the selected paragraph before/after. Oversize
reviews require smaller proposals and never expose truncated approval buttons.
Cancellation, revision, expiry, conflict and uncertain execution have distinct
messages; uncertainty never claims no write or invites duplicate submission.
Verified receipts show a link and brief summary instead of the full diff.

Default Chinese and concise submission replies follow the Skill, preserving its
current-page reread, conditional citations, native search, catalog pagination and
CRM boundaries. User-approved Owner plus Test represents member acceptance for
this increment. Only changed behavior needs synthetic/live retesting; full-space
directory-enumeration permission remains deferred. Existing pages and old ordinary
messages are not rewritten. Production installation/restart requires a separate
maintenance approval after independent review, exact-SHA CI, a three-source
candidate and a concrete installation/rollback packet; preserve business ledgers.

Synthetic integration tests cover a meeting-to-capture-to-publish-to-read journey,
source grounding, missing evidence, separate customers, the distinction between
system-user and employee counts and between Excel export and an API, private-source
exclusion, unapproved/duplicate/expired approvals, stale previews, concurrent writes,
timeouts/partial writes, restart reconciliation, and gateway identity isolation.
Live tenant capability and the approved two-account real workflow remain release
acceptance steps, never inferred from mock tests or a successful build. Adding
real members requires their own gateway mapping, subject policy and authorization;
five-member fixture coverage is not five-member live or load-test evidence.
