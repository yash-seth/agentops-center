# 1. LangGraph for agent orchestration

**Status:** accepted

## Context
The agents need planning, tool execution, memory and RAG, and the platform needs to observe, limit
and replay them. Candidates: LangGraph, CrewAI, AutoGen, a hand-rolled loop.

## Decision
Use LangGraph with a small explicit graph (model node, tool node, conditional edges) and execute
tools in our own node rather than a prebuilt one.

## Consequences
- The control flow is an explicit state machine, so loop detection, step budgets and stopping
  gracefully are straightforward to add at node boundaries.
- Because every model response and tool result passes through our nodes, we can record each step and
  replay a run exactly by substituting recorded responses.
- Owning the tool node lets every call get a span, metrics, timeout, retries, a circuit breaker and
  fault injection in one place.
- Trade-off: more code than a high-level multi-agent framework, and we do not get role-based crew
  abstractions. For operational control that is the right trade here.
