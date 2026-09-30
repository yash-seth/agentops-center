# Runbook

Each alert in `deploy/prometheus/alerts.yml` links to a section here. Work from the alert to the
affected agent and version, then to example runs and their traces.

## Low success rate
Alert: `AgentSuccessRateLow`.
1. Grafana "Agent health": is it one agent version or all? Compare the version panel.
2. Was there a deployment or prompt change in the last hour? If yes, roll the version back first,
   then investigate.
3. Check the tool failure panel: a failing tool usually explains the drop.
4. Open an example failing run's trace (`aoc.run.status != ok`) and find the first errored span.

## Tool failures
Alert: `ToolFailureRateHigh`.
1. Identify the tool and agent from the alert labels.
2. Look at the errored tool spans: the exception event has the message. `injected failure` means a
   chaos fault is active (`AOC_CHAOS`); clear it.
3. Downstream dependency down? Check the dependency's own health before changing the agent.
4. Mitigation options: fall back to a cached or read-only path, or disable the tool for the agent.

## High latency
Alert: `AgentLatencyP95High`.
1. Split latency by tool (`aoc_tool_duration_seconds`) to see whether a tool or the model is slow.
2. Check the LLM provider status and whether fallback to another provider occurred.
3. Look for runs with many steps: slow runs are often near-loops.

## Loops
Alert: `AgentLoopDetected`.
1. The loop guard already stopped the run and returned a safe fallback answer.
2. The `loop_detected` span event has the reason: `repeat` (same tool and args), `oscillation`,
   `step_budget` or `cost_budget`.
3. Open the run's trace and read the tool inputs and outputs to see why the model kept retrying;
   commonly a tool returns an error the model does not know how to handle.
4. Fix the prompt or tool error message, ship a new agent version and compare success rate.

## Cost overrun
Alert: `DailyCostOverBudget`.
1. Break down cost by tenant, agent and model (FinOps view).
2. Look for loops or unusually long runs; check `aoc_run_cost_usd` p95.
3. Consider routing simple questions to a cheaper model or lowering `limits.cost_budget_usd`.
