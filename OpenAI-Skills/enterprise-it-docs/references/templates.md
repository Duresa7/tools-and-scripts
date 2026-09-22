# Reusable documentation templates

Use these templates as patterns, not rigid forms. Keep the metadata block and adapt the remaining sections to the asset type and task.

## Hybrid handoff template

```markdown
# [System or Environment Title]

## Metadata
- **Author:**
- **Date Created:**
- **Last Updated:**
- **Document Purpose:** Hybrid handoff
- **Asset / System Name:**
- **Asset Type:**
- **Environment:**
- **Primary Platform:**
- **Owner / Team:**
- **Source Material:**

## Overview

## Current State

## Network Configuration

## Roles / Services

## Security Configuration

## Dependencies

## Validation

## Risks / Known Issues

## Next Actions
```

## Implementation record template

```markdown
# [Build or Change Record Title]

## Metadata
- **Author:**
- **Date Created:**
- **Last Updated:**
- **Document Purpose:** Implementation record
- **Asset / System Name:**
- **Asset Type:**
- **Environment:**
- **Primary Platform:**
- **Owner / Team:**
- **Source Material:**

## Overview

## Scope

## Configuration Actions

## Resulting State

## Security and Hardening Notes

## Validation

## Known Gaps

## Follow-Up Actions
```

## Network-focused template

```markdown
# [Network Asset or Segment Title]

## Metadata
- **Author:**
- **Date Created:**
- **Last Updated:**
- **Document Purpose:** Environment reference
- **Asset / System Name:**
- **Asset Type:**
- **Environment:**
- **Primary Platform:**
- **Owner / Team:**
- **Source Material:**

## Overview

## Management and Access

## Interfaces / VLANs / Addressing

## Routing / NAT / ACLs

## Dependencies and Upstream / Downstream Systems

## Validation

## Risks / Known Issues

## Next Actions
```

## Compression examples

Convert noisy notes into concise operational language:

- Instead of: `Clicked through the setup wizard and picked the default options for most prompts.`
  Use: `Completed base installation using standard defaults where no custom configuration was required.`

- Instead of: `I kept testing ping over and over and it finally started working.`
  Use: `Validated Layer 3 reachability after address and gateway configuration.`

- Instead of: `Installed a bunch of tools for admin and security stuff.`
  Use: `Installed the required administration and security tooling to support management and validation workflows.`
```
