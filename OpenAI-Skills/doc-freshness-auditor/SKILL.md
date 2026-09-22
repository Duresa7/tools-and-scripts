---
name: doc-freshness-auditor
description: analyze multiple markdown and word documents to detect outdated, superseded, duplicated, conflicting, or unclear content and recommend whether to keep, update, archive, delete, mark as superseded by a newer doc, or request human confirmation. use when comparing related docs from chat uploads or google drive and the user wants a structured review of stale information, overlap, inaccurate details, replacement candidates, or documentation drift. works across technical and non-technical documentation while still handling homelab, infrastructure, networking, cloud, and operational notes especially well.
---

# Doc Freshness Auditor

## Overview

Review related documents and determine whether they still appear accurate, current, and worth keeping. Compare documents against one another, identify likely overlap or contradiction, and recommend what should happen next without making destructive assumptions.

This skill is general-purpose. It should work for technical and non-technical documentation alike. Use technical anchors such as hostnames, versions, or configurations when they exist, but do not assume every document set is infrastructure-oriented.

## Core workflow

Follow this sequence:

1. Gather the candidate documents from the files the user uploaded or from connected sources the user requested.
2. Read enough of each document to understand what entity, process, project, system, topic, or decision it describes.
3. Build a relationship map across documents before judging freshness.
4. Identify signals of recency, contradiction, supersession, duplication, drift, or missing updates.
5. Produce structured recommendations with evidence and confidence.
6. Ask the user targeted clarification questions whenever confidence is low or the documents leave important ambiguity unresolved.

## Relationship mapping

Before labeling anything as stale, determine whether two or more documents are related.

Look for clusters of matching signals such as:

- repeated names, identifiers, titles, projects, systems, products, owners, teams, dates, version references, or recurring terminology
- repeated machine identifiers, hostnames, instance names, VM names, IP addresses, URLs, ports, repository names, service names, environment names, database names, or path names when the material is technical
- repeated config details, package versions, commands, architecture descriptions, workflows, decisions, responsibilities, or deployment steps
- similar titles, section headings, markdown anchors, or document structure
- wording that implies succession, replacement, migration, deprecation, retirement, repurposing, revision, archival intent, rollback, or historical context

Do not rely on one weak match alone. Prefer clusters of signals.

## Freshness and conflict rules

Treat a document as potentially outdated when one or more of these conditions are present:

- a newer document describes the same subject with materially different facts
- a document describes an earlier state, owner, configuration, workflow, or role that appears to have changed elsewhere
- dates, versions, ownership, topology, addresses, procedures, or responsibilities conflict across related docs
- one document presents historical setup information as if it were still current
- maintenance signals are absent in an older document while newer related docs contain change history, replacement guidance, or revised operating truth

Treat a document as potentially still valid when:

- the differences can be explained by environment, timeframe, audience, scope, or document purpose
- one document is clearly historical, archival, or migration-focused and says so explicitly
- the documents describe different layers of the same system or process rather than conflicting facts
- one doc is a summary and another is a detailed procedure, with no substantive contradiction

Never recommend deletion based on a single contradiction unless the evidence is very strong and there is an obvious replacement document. Prefer archive or needs human confirmation when uncertainty remains.

## Required decision labels

Use these labels as the primary recommendation set:

- keep
- update
- archive
- delete
- superseded by newer doc
- needs human confirmation

You may also add a short secondary note when useful, such as duplicate, partial overlap, historical reference, possible merge candidate, unclear scope, or conflicting timeline.

## Confidence guidance

Use a simple confidence level for every finding:

- high: multiple concrete signals support the conclusion and no major unresolved contradiction remains
- medium: the evidence is meaningful but at least one plausible alternate interpretation exists
- low: the signals are weak, incomplete, or context-dependent

If confidence is low, ask a clarification question instead of making a firm recommendation.
If the recommendation is delete or superseded by newer doc, include the specific evidence that makes that recommendation credible.

## Output format

Unless the user asked for a different format, present the review in a structured, scan-friendly layout with markdown tables.

Use this report structure:

# Document freshness review

## Executive summary

Summarize the main risks, the strongest likely supersessions, and any places where user clarification is required before acting.

## Findings table

Use a markdown table with these columns whenever practical:

| document | related document | issue type | key finding | evidence | confidence | recommended label | next action | question for you |
|---|---|---|---|---|---|---|---|---|

Guidance:
- keep cells concise enough to scan quickly
- if evidence is too long, summarize it in the table and add a short bulleted appendix below
- if multiple related docs are involved, either add multiple rows or use a group name and explain the group below
- always fill the confidence and recommended label columns
- use `none` in question for you when no clarification is needed

## Relationship groups

When several documents belong together, add a short section after the table that explains the grouping logic.

## Label summary

Add a compact summary table:

| label | count |
|---|---:|
| keep | 0 |
| update | 0 |
| archive | 0 |
| delete | 0 |
| superseded by newer doc | 0 |
| needs human confirmation | 0 |

Populate the counts from the findings.

## Clarifications needed

If ambiguity remains, ask only the minimum questions needed to resolve important uncertainty. Make the questions concrete and easy to answer.

## Optional appendix

Use this only when needed for readability:
- evidence notes
- grouped timelines
- duplicate-cluster notes
- rationale for medium or low confidence items

## Decision behavior

When there is uncertainty:

- explicitly say what is known
- explicitly say what is uncertain
- explain what additional context would change the recommendation
- ask the user a direct question

Do not pretend certainty. Do not silently collapse nuanced situations into one label.

## Reasoning priorities

Prioritize factual consistency over writing style.
Prioritize evidence from the documents over assumptions.
Prioritize reversible recommendations over destructive ones.
Prefer update or archive over delete when the document may still have historical value.
Prefer clear table output over long prose when the user needs a quick review.

## Connector and file handling

When documents are available in the current chat, inspect those first.
When the user wants files from connected sources such as google drive, retrieve and compare those as well.
If both uploaded files and connected files are in scope, compare across both sets.

## Personalization guidance

The user often works across a homelab and adjacent operational contexts such as networking, virtualization, cloud, windows server, identity, and infrastructure changes. This makes change drift and documentation supersession common.

Use that context to improve review quality when it helps, but do not force every audit into an infrastructure frame. The same review method should still work for project docs, process notes, planning docs, and general reference material.

## Examples

### Example 1

Scenario:
- an older markdown doc says a VM hosts service A on a specific machine
- a newer word doc says the same machine was repurposed for service B

Good recommendation:
- mark the older doc as superseded by newer doc or update, depending on whether the old content should be retained for history
- ask whether the old document should remain as a migration record

### Example 2

Scenario:
- two markdown docs describe similar setup steps but with different package versions and ports
- neither doc clearly states which environment it applies to

Good recommendation:
- mark both as needs human confirmation or update
- ask whether one is dev and the other is prod, or whether one replaced the other

### Example 3

Scenario:
- two project notes describe the same recurring workflow with different owners and different approval steps
- neither document clearly says it replaced the other

Good recommendation:
- mark both as update or needs human confirmation depending on the strength of the overlap
- ask which workflow is currently authoritative

## Resources

For detailed review heuristics and question patterns, consult `references/review-rubric.md`.
