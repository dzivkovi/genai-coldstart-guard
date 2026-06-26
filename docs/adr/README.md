# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for `genai-coldstart-guard`.

## What are ADRs?

ADRs document significant architectural decisions made during development, capturing:

- **Context:** Why we needed to make a decision
- **Decision:** What we chose to do
- **Consequences:** Trade-offs and implications
- **Alternatives:** What we considered and rejected

## Format

We use the [Michael Nygard template](template.md):

- **Status:** proposed | accepted | rejected | deprecated | superseded
- **Date:** When the decision was made
- **Context:** The problem or opportunity
- **Decision:** What we're doing
- **Consequences:** Impact (positive and negative)
- **Alternatives:** Options we evaluated
- **Affects:** Source files changed
- **Related Debt:** Todos spawned

## Naming Convention

Files are named: `ADR-NNNN-title-with-dashes.md`

## Current ADRs

| ADR | Title | Status | Date |
| --- | ----- | ------ | ---- |
| [ADR-0001](ADR-0001-cold-start-facade-poc.md) | Cold-start facade POC for Databricks GenAI serving endpoints | proposed | 2026-06-25 |

## Process

1. **Identify Decision:** Architecture-level choices that impact system design
2. **Draft ADR:** Use [template.md](template.md), fill in all sections
3. **Decide:** Update status to "accepted" or "rejected"
4. **Commit:** Check into Git with descriptive commit message

## Relationship to Other Documentation

- **`README.md`**: User-facing guide (WHAT it does, HOW to use it)
- **`CLAUDE.md`**: Operational context for Claude Code (commands, packaging, release)
- **`ARCHITECTURE.md`**: System overview and vision (WHERE it's heading)
- **`docs/adr/`**: Decisions (WHY we chose this approach)
- **[`docs/testing.md`](../testing.md)**: Testing discipline and the retrieval-eval harness (HOW we measure that we're still on track)

## Working with AI Assistants

### How to Ask Claude to Create an ADR

**Good prompt:**

```text
Create an ADR for [decision]. Use the template at docs/adr/template.md.
Include these alternatives we discussed: [list alternatives].
```

**What Claude needs to know:**

1. The decision you made
2. Why you needed to make it (context/problem)
3. What alternatives you considered
4. Which files were affected and any debt spawned

**Common mistake:** Asking "document this decision" without specifying template.
Claude might create a generic markdown file instead of following Michael Nygard format.

## References

- [ADR GitHub Organization](https://adr.github.io/)
- [Joel Parker Henderson's ADR Repo](https://github.com/joelparkerhenderson/architecture-decision-record)
