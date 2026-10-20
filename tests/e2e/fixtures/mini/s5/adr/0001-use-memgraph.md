---
status: accepted
date: 2026-10-06
---

# 1. Keep media files in the MediaCache

## Context and Problem Statement

The media store reads the same files many times.

## Decision Outcome

The MediaCache component keeps recently used media files in memory.
The implementation lives in src/main/java/mini/MediaCache.java.

## Consequences

Memory use grows with the number of cached files.
