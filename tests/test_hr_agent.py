
from aoc_runtime import semconv as sc
from aoc_runtime.llm import FakeChatModel
from aoc_runtime.runner import load_agent, run_agent


def test_hr_agent_answers_from_hr_docs_only(exporter):
    res = run_agent("hr-policy-bot", "How many days of sick leave do I get?", llm=FakeChatModel())
    assert res.status == "ok"
    assert res.tool_calls == ["rag_search"]
    assert "leave_policy.md" in res.answer
    assert "replenishment" not in res.answer


def test_agents_are_separate_tenants_and_apps(exporter):
    run_agent("hr-policy-bot", "travel reimbursement rules", llm=FakeChatModel())
    run_agent("supply-chain-assistant", "supplier lead time", llm=FakeChatModel())
    roots = {
        s.attributes[sc.AGENT_NAME]: s.attributes
        for s in exporter.get_finished_spans()
        if s.name == "agent.run"
    }
    assert roots["hr-policy-bot"][sc.TENANT_ID] == "snackco-hr"
    assert roots["hr-policy-bot"][sc.APP_ID] == "hr-helpdesk"
    assert roots["supply-chain-assistant"][sc.TENANT_ID] == "snackco-supply"


def test_specs_declare_only_tools_they_implement():
    for name in ("hr-policy-bot", "supply-chain-assistant"):
        spec, tools = load_agent(name)
        assert sorted(spec.tools) == sorted(t.name for t in tools)
