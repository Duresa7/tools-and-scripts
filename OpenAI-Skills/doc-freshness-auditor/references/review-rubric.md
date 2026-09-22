# Review rubric

Use this rubric to improve consistency when reviewing document freshness.

## 1. What is the document about?

Extract the most concrete anchors you can find:

- topic, process, project, system, service, host, machine, VM, environment, team, decision, or workflow
- identifiers such as names, dates, versions, IPs, hostnames, URLs, repos, file paths, owners, or roles
- explicit dates, update timestamps mentioned in the content, or change-log notes
- intended audience and document purpose

## 2. How related are two documents?

### Strong evidence of relatedness

- same subject with overlapping facts and identifiers
- same machine, host, VM, service, workflow, or environment
- same title or same core concept with one clearly newer or revised
- one document explicitly references replacement, migration, repurposing, revision, or deprecation of the other topic

### Medium evidence of relatedness

- same service family, process family, or workflow with overlapping steps
- same owner and same operational surface but differing specifics
- same broad topic plus several shared terms, but without a single decisive anchor

### Weak evidence of relatedness

- similar broad topic but no concrete shared anchors

Do not make strong supersession claims from weak evidence.

## 3. What kind of issue is present?

### Likely supersession

Use when a newer doc appears to replace the old operating truth for the same subject.

Typical signals:
- same subject, newer facts
- same identifier, new role or state
- explicit repurpose, migrate, replace, retire, deprecated, moved, revised, or now uses wording

### Likely update needed

Use when a doc is still useful but parts of it appear stale.

Typical signals:
- steps mostly align but versions, paths, owners, terms, responsibilities, or commands changed
- the document has good structure but inaccurate details

### Likely archive

Use when a doc still has historical value but should not be used as current guidance.

Typical signals:
- migration record, previous state, retirement note, incident history, old rollout plan, old procedure version
- content is no longer current but still useful for traceability

### Likely delete

Use sparingly.

Typical signals:
- pure duplicate with no meaningful extra context
- abandoned stub or copy that is clearly replaced and adds no historical value

If there is any meaningful operational or historical value, prefer archive.

### Needs human confirmation

Use when the evidence is incomplete, contextual, or plausibly explained in more than one way.

## 4. How to ask clarifying questions

Ask short questions that let the user resolve the ambiguity quickly.

Good patterns:
- "Is this the same machine after repurposing, or a different environment with the same naming pattern?"
- "Should this older doc remain as historical migration context, or should it stop being used entirely?"
- "Do these two docs describe dev vs prod, or is one intended to replace the other?"
- "Is the older document still referenced anywhere operationally?"
- "Which document is supposed to be the current source of truth?"

Avoid vague questions like:
- "Can you clarify?"
- "What should I do with this?"

## 5. Recommendation writing style

Recommendations should be practical and conservative.

Preferred style:
- state the issue plainly
- cite the specific conflict or overlap
- assign a confidence level
- recommend the least destructive next step that fits the evidence
- ask a question only if the answer materially affects the label

## 6. Preferred table row pattern

Use rows like this when possible:

| document | related document | issue type | key finding | evidence | confidence | recommended label | next action | question for you |
|---|---|---|---|---|---|---|---|---|
| old-vm-notes.md | repurposed-vm.docx | conflict / likely stale | older role likely replaced by newer purpose | both docs reference the same machine identifier but assign different roles | high | superseded by newer doc | archive old doc unless it is needed for migration history | should the older doc remain as historical context? |

## 7. Labeling discipline

- keep should be used when the doc still appears accurate and useful
- update should be used when the doc is still relevant but not fully current
- archive should be used when the doc has historical value but should not guide current work
- delete should be used only when duplication or obsolescence is very strong and historical value is negligible
- superseded by newer doc should be used when a specific newer document clearly appears to replace it
- needs human confirmation should be used whenever the correct recommendation depends on missing context
