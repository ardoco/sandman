#!/bin/sh
# Fails if a banned agent framework is referenced (ADR-0017). Run from libs/go/sandman-agent.
! grep -rEi "langchaingo|cloudwego/eino|llama[i]ndex|lang[g]raph" . --include=go.mod --include='*.go'
