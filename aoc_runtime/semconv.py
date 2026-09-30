"""Attribute names used across all telemetry. Single source of truth; see docs/SEMCONV.md.

We follow the OpenTelemetry GenAI semantic conventions (``gen_ai.*``) where they exist and use
the ``aoc.*`` namespace for operations-center concepts the spec doesn't cover (agent version,
tenant, run, loop detection, cost).
"""

# --- OTel resource / standard ---
SERVICE_NAME = "service.name"
DEPLOYMENT_ENVIRONMENT = "deployment.environment"

# --- OTel GenAI semantic conventions ---
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"

# --- Operations-center conventions ---
AGENT_NAME = "aoc.agent.name"
AGENT_VERSION = "aoc.agent.version"
PROMPT_VERSION = "aoc.prompt.version"
TENANT_ID = "aoc.tenant.id"
APP_ID = "aoc.app.id"
RUN_ID = "aoc.run.id"
RUN_STATUS = "aoc.run.status"
RUN_STEPS = "aoc.run.steps"
TOOL_NAME = "aoc.tool.name"
TOOL_STATUS = "aoc.tool.status"
COST_USD = "aoc.cost.usd"
LOOP_DETECTED = "aoc.loop.detected"
RETRIEVAL_TOP_K = "aoc.retrieval.top_k"
RETRIEVAL_STORE = "aoc.retrieval.store"

# Attributes that must be present on every span emitted during an agent run.
REQUIRED_RUN_ATTRIBUTES = (
    AGENT_NAME,
    AGENT_VERSION,
    PROMPT_VERSION,
    TENANT_ID,
    APP_ID,
    RUN_ID,
    DEPLOYMENT_ENVIRONMENT,
)
