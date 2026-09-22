---
name: enterprise-it-docs
description: create concise, professional markdown infrastructure and security documentation using formal enterprise it and cybersecurity terminology. use when the user wants a reusable source-of-truth document, build summary, operational handoff, configuration record, lab note, system implementation report, or environment documentation for servers, endpoints, virtual machines, network devices, cloud resources, security tools, or mixed environments. works from current chat context, uploaded files, pasted notes, and connected document sources such as google drive.
---

# Enterprise IT Docs

## Overview

Create high-signal markdown documentation that reads like internal enterprise infrastructure or cybersecurity documentation. Prefer durable facts, resulting configuration state, decisions, validation outcomes, dependencies, and next actions over conversational filler or play-by-play narration.

## Core standard

Write in a formal, technical enterprise style.

Use terminology common to IT operations, platform engineering, network administration, systems administration, and cybersecurity teams. Examples include terms such as asset, host, endpoint, workload, interface, subnet, VLAN, ACL, service account, baseline, hardening, validation, dependency, upstream, downstream, rollback, known issue, exposure, control, segmentation, and operational impact.

Avoid:
- casual phrasing
- motivational language
- redundant recap
- obvious statements with no operational value
- fabricated values when the source material is incomplete

When information is missing, explicitly mark it as `TBD`, `Not provided`, or `Unable to verify from available inputs`.

## Output modes

Choose one of these modes based on the request and available material.

1. **Operational summary**
   - Use for quick-reference documentation.
   - Emphasize current state, purpose, critical settings, dependencies, validation, and follow-up actions.
   - Keep implementation detail brief.

2. **Implementation record**
   - Use for source-of-truth build or change documentation.
   - Include important implementation details, configuration actions, security decisions, validation results, and known limitations.
   - Summarize low-value procedural noise instead of logging every trivial action.

3. **Hybrid handoff**
   - Default when the user does not specify.
   - Start with a concise operational view, then include a compact implementation section.

## Required metadata block

Always include a metadata section near the top of the document.

Use this default structure and adapt field names only when the asset type clearly requires it:

```markdown
## Metadata
- **Author:** [name if known]
- **Date Created:** [date]
- **Last Updated:** [date/time if known]
- **Document Purpose:** [build record | operational handoff | environment reference | incident support note | other]
- **Asset / System Name:** [hostname, VM name, device name, project asset, or environment label]
- **Asset Type:** [vm | server | workstation | firewall | switch | router | cloud instance | security appliance | lab environment | other]
- **Environment:** [production | staging | development | lab | other]
- **Primary Platform:** [windows | linux | macos | proxmox | network device OS | cloud platform | mixed]
- **Owner / Team:** [if known]
- **Source Material:** [chat context, uploaded files, google drive, pasted notes, mixed]
```

If the author is not explicitly provided but the user is clearly the operator, infer the author from the conversation only when appropriate. Otherwise mark as `Not provided`.

## Section selection logic

Do not force every document into the same rigid layout. Use the required metadata block, an overview, and then select only the sections that materially improve future reuse.

### Usually include
- Overview
- Scope or purpose
- Current state / resulting configuration
- Network details when applicable
- Security posture / hardening when applicable
- Dependencies and integrations
- Validation / testing
- Risks, gaps, or known issues
- Next actions / future changes

### Include when relevant
- Asset inventory
- Roles and services
- Installed components
- Storage layout
- Identity and access model
- Firewall / ACL / segmentation notes
- Certificates / DNS / DHCP / routing
- Backup / snapshot / recovery notes
- Monitoring / logging
- Troubleshooting notes
- Change history
- Reference commands or file paths

### Usually exclude unless clearly valuable
- trivial click-by-click UI narration
- repetitive command output with no insight
- generic explanations of standard technology
- speculative assumptions not grounded in the source material
- secrets, passwords, private keys, full tokens, or sensitive values that should not be retained

## Information hierarchy

Prefer this order:

1. What the asset or environment is for
2. What was configured or changed
3. What the resulting state is now
4. What another engineer needs to know before modifying it
5. What remains unresolved or planned

When source material contains both actions and outcomes, prioritize outcomes. Convert long activity logs into succinct statements such as:
- `Configured static addressing on the management interface and validated gateway reachability.`
- `Applied baseline hardening controls and disabled unnecessary remote management paths.`
- `Joined the host to the domain and verified policy application.`

## Source handling

Use all relevant available sources the user points to or provides.

### Current chat
Treat the current conversation as primary input when the user asks to generate documentation from what was discussed or performed in chat.

### Uploaded files
Use uploaded notes, exported configs, screenshots, reports, or command logs when provided.

### Connected sources
Use connected sources such as Google Drive when the user requests documentation from those materials.

When combining sources:
- merge duplicates
- resolve contradictions conservatively
- favor the most specific and best-supported version
- note unresolved discrepancies in a short `Open questions` or `Known gaps` section

## Writing workflow

1. Identify the asset, scope, and requested output mode.
2. Extract durable facts: names, roles, versions, addresses, services, controls, dependencies, validation evidence, and follow-up items.
3. Remove noise, duplicate statements, and low-value narration.
4. Choose sections that fit the asset and task.
5. Write the markdown document in formal enterprise style.
6. Perform a quality pass to remove filler and verify that the document can serve as a future reference.

## Markdown style rules

- Use clear `#`, `##`, and `###` headings.
- Prefer bullet lists for configuration state, dependencies, risks, and validations.
- Use short tables only when they improve scanability, such as IP addressing, interfaces, services, ports, or system inventory.
- Use backticks for commands, paths, registry keys, service names, ports, hostnames, and configuration identifiers.
- Keep paragraphs short.
- Keep chronology minimal unless it matters operationally.

## Default document skeleton

Use this as a starting point and adapt it.

```markdown
# [System or Environment Title]

## Metadata
- **Author:**
- **Date Created:**
- **Last Updated:**
- **Document Purpose:**
- **Asset / System Name:**
- **Asset Type:**
- **Environment:**
- **Primary Platform:**
- **Owner / Team:**
- **Source Material:**

## Overview
[What this asset/environment is, why it exists, and what was done.]

## Current State
[High-value summary of the resulting configuration and operational role.]

## Technical Details
[Selected subsections only as relevant: network, services, storage, identity, security, dependencies, integrations.]

## Validation
[How the configuration or state was verified.]

## Risks / Known Issues
[Only real risks, limitations, or gaps.]

## Next Actions
[Planned follow-up work or recommended future changes.]
```

## Asset-specific guidance

### Servers, workstations, and VMs
Often include:
- hostname / VM name
- OS and version
- hypervisor or platform
- CPU / memory / storage when relevant
- interface and IP details
- installed roles or applications
- local or domain join state
- management access method
- hardening or endpoint controls

### Network devices
Often include:
- device role
- management IP
- interfaces / VLANs / routing context
- ACLs, NAT, segmentation, and upstream/downstream dependencies
- authentication method
- logging / monitoring targets
- backup or config export location

### Security tooling or lab environments
Often include:
- purpose and test objective
- trust boundaries
- exposure assumptions
- telemetry / logging configuration
- containment notes
- known safe-use constraints
- cleanup or rollback considerations

## Quality bar

Before finalizing, verify that the document:
- can be used by another IT or security professional without extra explanation
- makes the current state easy to understand quickly
- preserves implementation details only when they matter later
- uses accurate technical terminology
- avoids filler and avoids exposing sensitive secrets

## Example user requests

- `Create a source-of-truth markdown report for this Windows Server build from our chat history.`
- `Turn these notes and uploaded screenshots into a concise operational handoff for a Proxmox VM.`
- `Use my Google Drive notes and the current chat to document this lab environment in enterprise markdown.`
- `Generate a hybrid build record and quick-reference doc for this firewall configuration.`
