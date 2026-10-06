---
status: accepted
date: 2026-10-06
---

# Implementation language per component

## Context and Problem Statement

Each agent must be implemented in exactly one language, Python or Java, and the number of languages must stay small. If both are used, a shared agent library per language must implement the same contracts. Which language does each component use?

## Decision Drivers

* Reuse of existing TLR and inconsistency detection research code (ArDoCo, LiSSA: Java 21; ArDoCo core on Maven Central as `io.github.ardoco`).
* Ecosystem fit: document/code parsing and LLM prompt iteration (Python strong), A2A SDK maturity (Python is the reference SDK; Java server SDK is Quarkus/CDI-based).
* Maintenance cost of two agent libraries and contract parity.
* Team expertise (KIT group: Java for ArDoCo, Python for LLM experiments).

## Considered Options

* A: Python for orchestrator, extraction agent, query service; Java for TLR and inconsistency agents.
* B: All Python; ArDoCo/LiSSA run as separate tool containers (Java CLI/HTTP wrappers) called by Python agents.
* C: All Java.

## Decision Outcome

Chosen option: "A", because the two agents whose core is existing Java research code run it in-process (direct access to pipeline APIs and intermediate data, no wrapper services, no JVM start per call), while the components that are mostly LLM prompting and parsing use Python's stronger ecosystem.

### Consequences

* Good, because ArDoCo/LiSSA approaches and inconsistency detection plug in without adapters across process boundaries.
* Good, because the Python parts use the reference A2A SDK and the strongest parsing libraries (Docling, tree-sitter language pack).
* Bad, because two agent libraries must be maintained and kept in parity (mitigated by ADR-0010: shared schemas and conformance vectors).
* Bad, because Java agents need Quarkus for the A2A server (ADR-0002).
* Neutral, because LiSSA and ArDoCo `llm-access` use LangChain4j internally as LLM client; accepted (Q-2) inside approach/detector adapters, configured to the gateway; agent code itself uses the SANDMAN library.
* Neutral, because code extraction and further detectors come from plugged-in services (Q-5, Q-6) whose language is free; they are not SANDMAN agents (ADR-0012).

### Confirmation

Conformance suite passes in both languages; a Java agent stub and a Python orchestrator stub interoperate in the walking skeleton.

## Pros and Cons of the Options

### A: Python + Java split

* Good, because each component uses the ecosystem that fits its core work.
* Bad, because two libraries and two build chains.

### B: All Python, Java tools as containers

* Good, because one agent library, one build chain, no parity problem.
* Bad, because ArDoCo/LiSSA need Java wrapper services anyway (Java code still exists, just not "agent" code), with a coarse CLI/HTTP interface and serialization of inputs.
* Bad, because new ArDoCo-based approaches need wrapper changes.

### C: All Java

* Good, because one library; ArDoCo in-process everywhere.
* Bad, because document parsing (PDF/DOCX layout) and fast LLM prompt iteration are weaker; the A2A Java server ties every agent to Quarkus.

## More Information

* Q-1 answered 2026-10-06: ArDoCo/LiSSA run in Java agents. Q-2 answered: LangChain4j inside reused code is acceptable.
* Related: ADR-0002, ADR-0010, ADR-0012.
