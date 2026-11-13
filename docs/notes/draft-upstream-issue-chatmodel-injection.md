# Draft: upstream issue, ChatModel injection for the Artemis checks

Status: draft. Do not post. The repository owner decides if and where to post it.

Target: ArDoCo/ardoco (or the repository that holds the programming-language and architectural-style checks).
Pin tested: ArDoCo `21ef682` (PR #186).

## Title

Allow a caller to inject the `ChatModel` into the Artemis inconsistency checks

## Problem

The checks for programming language and architectural style create their LLM stages inside the library.
The caller cannot supply its own `ChatModel`.
SANDMAN sends every LLM call through its own gateway.
The gateway enforces the policy, the audit log and the cost limits.
A library that builds its own client bypasses all of them.

## Current workaround

SANDMAN builds the stages from the public classes.
It passes a `ChatModel` that wraps the gateway client.
This works, but it copies the wiring code of the checks.
Each upstream change to the wiring needs a manual merge on the SANDMAN side.

## Proposal

Add one constructor or one factory method per check that takes a `ChatModel`.
Keep the current behaviour as the default.
Let the tool-calling agents (read-only code tools) use the same injected model.

## Acceptance

- A test passes a stub `ChatModel` and sees every request on the stub.
- No code path creates a second client.
- The default behaviour does not change.

## Evidence

SANDMAN keeps the workaround in `services/inconsistency-agent/detector-ardoco` (`ArtemisChatModel`, `ArtemisRunner`).
See `docs/notes/spike-T3.9.md` for the cost and the cassette results.
