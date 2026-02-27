---
name: rfc-sip-lookup
description: Look up SIP/RTP/SDP protocol details across RFC text files using parallel agents. Use when researching protocol specs, debugging SIP compliance, or checking RFC requirements.
user-invocable: true
---

# RFC SIP/RTP/SDP Lookup

You are looking up protocol details from RFC text files vendored in `rfcs/`.

## Step 1: Consult the Index

Read `.claude/skills/rfc-sip-lookup/rfc-index.md` to identify which RFC sections are relevant to the user's query. The index maps topic areas (registration, INVITE, SDP, RTP, timers, etc.) to specific files and line ranges.

## Step 2: Dispatch Parallel Explore Agents

Launch 2–3 Explore agents via the Task tool (`subagent_type: "Explore"`) to read the relevant RFC sections in parallel. Each agent should:

- Read the specific line range(s) from the index using the Read tool with `offset` and `limit` parameters
- Search for specific keywords within the identified sections using Grep
- Extract the precise normative text (MUST, SHOULD, MAY statements), protocol rules, or message format details relevant to the query

Structure each agent prompt like:

> Read rfcs/rfc3261.txt lines 7471–7633 (§17.2.1 INVITE Server Transaction).
> Find details about: [user's specific question].
> Return the exact normative requirements, relevant ABNF, and any examples.

Dispatch agents for different RFC files in parallel. If the query spans multiple topics (e.g., "INVITE with SDP offer"), send one agent for the SIP sections and another for the SDP sections.

## Step 3: Synthesize

Combine the agents' findings into a concise answer:

- Lead with the direct answer to the user's question
- Include exact RFC section references (e.g., "RFC 3261 §17.2.1, line 7520")
- Quote key normative language verbatim when precision matters
- Note any cross-references between RFCs (e.g., SIP→SDP, SIP→Digest Auth)
- If the query is about implementation, connect the RFC requirements to what the code should do

## Tips

- For broad queries ("how does INVITE work?"), focus on the primary sections and summarize rather than reading everything
- For narrow queries ("what's Timer G's default value?"), target the exact line range and quote the text
- RFC 3665 (call flow examples) is the best companion to RFC 3261 — check it for annotated message exchanges
- The smaller RFCs (2617, 3264, 3551) can often be read in their entirety by a single agent
