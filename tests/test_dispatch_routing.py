from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parent.parent / "octopus-api.governance_engine.py"
SPEC = spec_from_file_location("governance_engine", MODULE_PATH)
assert SPEC and SPEC.loader
governance_engine = module_from_spec(SPEC)
SPEC.loader.exec_module(governance_engine)


def test_greeting_routes_to_charlie_only() -> None:
    plan = governance_engine._build_dispatch_plan("Atlas are you here?")

    assert plan.primary_agent == "charlie"
    assert plan.response_agents == ("charlie",)
    assert plan.execution_targets == ()
    assert plan.mode == "direct_response"
    assert plan.classification == "greeting"


def test_product_question_stays_with_charlie() -> None:
    plan = governance_engine._build_dispatch_plan(
        "What should TitanFlow prioritize for launch strategy?"
    )

    assert plan.primary_agent == "charlie"
    assert plan.response_agents == ("charlie",)
    assert plan.execution_targets == ()
    assert plan.classification == "product_strategy"


def test_fix_this_bug_routes_to_ollie_spec_dispatch() -> None:
    plan = governance_engine._build_dispatch_plan(
        "Fix this bug in the onboarding form button state."
    )

    assert plan.primary_agent == "charlie"
    assert plan.response_agents == ("charlie",)
    assert plan.execution_targets == ("ollie",)
    assert plan.mode == "spec_then_dispatch"
    prompt = governance_engine._build_dispatch_prompt(
        "Fix this bug in the onboarding form button state.",
        plan,
    )
    assert "Execution targets: Ollie" in prompt
    assert "Do not solve it yourself." in prompt


def test_ssh_request_routes_to_flow_spec_dispatch() -> None:
    plan = governance_engine._build_dispatch_plan(
        "ssh in and restart nginx on Mercury"
    )

    assert plan.primary_agent == "charlie"
    assert plan.response_agents == ("charlie",)
    assert plan.execution_targets == ("flow",)
    assert plan.mode == "spec_then_dispatch"
    assert plan.classification == "infra_backend"


def test_mentions_override_classifier() -> None:
    plan = governance_engine._build_dispatch_plan(
        "@Archie fix the release architecture plan"
    )

    assert plan.source == "mention"
    assert plan.mention_target == "archie"
    assert plan.response_agents == ("archie",)
    assert plan.execution_targets == ()


def test_code_and_infra_dispatches_in_parallel() -> None:
    plan = governance_engine._build_dispatch_plan(
        "Fix the ATLAS chatbox CSS and deploy the update on Mercury."
    )

    assert plan.execution_targets == ("ollie", "flow")
    assert plan.mode == "spec_then_dispatch"
    assert plan.classification == "code_and_infra"
    prompt = governance_engine._build_dispatch_prompt(
        "Fix the ATLAS chatbox CSS and deploy the update on Mercury.",
        plan,
    )
    assert "=== OLLIE SPEC ===" in prompt
    assert "=== FLOW SPEC ===" in prompt


class _FakeBus:
    def __init__(self) -> None:
        self.handlers = {}

    def subscribe(self, topic, handler) -> None:
        self.handlers[topic] = handler


@pytest.mark.asyncio
async def test_dispatch_worker_consumer_only_forwards_translated_spec() -> None:
    bus = _FakeBus()
    consumer = governance_engine.DispatchWorkerConsumer(bus)
    received = {}

    async def ollie_handler(payload):
        received["payload"] = payload

    consumer.register_executor("ollie", ollie_handler)
    consumer.start()

    event = {
        "event_type": "gov_dispatch_target",
        "decision_id": "GOV-0042",
        "room_id": "governance",
        "actor": "papa",
        "target": "ollie",
        "spec": "OBJECTIVE:\\nRefine the UI bubble state.\\nEXECUTION:\\nPatch the component.",
        "dispatch": {"classification": "ui_frontend", "mode": "spec_then_dispatch"},
        "intent": "Fix this bug in the onboarding form button state.",
    }
    await bus.handlers["gov_dispatch_target"](event)

    payload = received["payload"]
    assert payload["target"] == "ollie"
    assert payload["spec"].startswith("OBJECTIVE:")
    assert payload["dispatch"]["classification"] == "ui_frontend"
    assert "intent" not in payload
    assert "raw_user_text" not in payload
    assert "Fix this bug" not in str(payload)


def test_split_dispatch_specs_produces_independent_target_specs() -> None:
    spec_text = """=== OLLIE SPEC ===
OBJECTIVE:
Polish the ATLAS chat bubble layout.
CONTEXT:
This is a frontend-only shell change.
CONSTRAINTS:
Do not touch backend routing.
EXECUTION:
Patch the component CSS and verify desktop/mobile.
VERIFICATION:
Capture browser screenshots.
=== FLOW SPEC ===
OBJECTIVE:
Deploy the approved shell update to Mercury.
CONTEXT:
This is an infra/deployment action.
CONSTRAINTS:
Do not edit frontend logic.
EXECUTION:
Back up the live file, upload the patch, and reload the service if required.
VERIFICATION:
Confirm the public route serves the new build.
"""
    split_specs = governance_engine._split_dispatch_specs(spec_text, ("ollie", "flow"))

    assert set(split_specs) == {"ollie", "flow"}
    assert "frontend-only shell change" in split_specs["ollie"]
    assert "backend routing" in split_specs["ollie"]
    assert "Deploy the approved shell update to Mercury" in split_specs["flow"]
    assert "Patch the component CSS" not in split_specs["flow"]


def test_ambiguous_diagnostic_prompt_stays_in_reasoning_lane() -> None:
    plan = governance_engine._build_dispatch_plan("Why is the signup form broken on Mercury?")

    assert plan.classification == "reasoning"
    assert plan.response_agents == ("archie", "charlie")
    assert plan.execution_targets == ()


def test_ambiguous_action_prompt_still_splits_code_and_infra() -> None:
    plan = governance_engine._build_dispatch_plan(
        "Can you fix the signup form and deploy it on Mercury?"
    )

    assert plan.classification == "code_and_infra"
    assert plan.execution_targets == ("ollie", "flow")


def test_strategy_prompt_is_not_misclassified_as_general() -> None:
    plan = governance_engine._build_dispatch_plan(
        "Should we ship TitanDash now or wait a week?"
    )

    assert plan.classification == "product_strategy"
    assert plan.response_agents == ("charlie",)
    assert plan.execution_targets == ()


def test_investigation_prompt_does_not_jump_straight_to_flow() -> None:
    plan = governance_engine._build_dispatch_plan(
        "Investigate why ATLAS is timing out on Shadow."
    )

    assert plan.classification == "reasoning"
    assert plan.response_agents == ("archie", "charlie")
    assert plan.execution_targets == ()


def test_issue_triage_prompt_routes_to_reasoning() -> None:
    plan = governance_engine._build_dispatch_plan(
        "What should we do about the Atlas recall rail issue?"
    )

    assert plan.classification == "reasoning"
    assert plan.response_agents == ("archie", "charlie")


def test_cc_tasks_auto_fork_to_ollie_and_flow() -> None:
    plan = governance_engine._build_dispatch_plan(
        "@CC take this queue and execute the rollout patch."
    )

    assert plan.classification == "golden_role_factory"
    assert plan.primary_agent == "charlie"
    assert plan.execution_targets == ("ollie", "flow")
    assert plan.notify_agents == ("cc", "cx")
    assert plan.requires_executor_touch is True
    assert plan.close_guard_targets == ("ollie", "flow")
    assert plan.close_guard_policy == "all"
    assert plan.required_subagent_lanes == ("dash", "octa", "flow")
    assert plan.sweep_passes_required == 2


def test_chex_tasks_auto_fork_to_ollie_and_flow() -> None:
    plan = governance_engine._build_dispatch_plan(
        "owner: Chex. Push this through the factory lane."
    )

    assert plan.classification == "golden_role_factory"
    assert plan.execution_targets == ("ollie", "flow")
    assert plan.notify_agents == ("cc", "cx")
    assert plan.requires_executor_touch is True
    assert plan.required_subagent_lanes == ("dash", "octa", "flow")
    assert plan.sweep_passes_required == 2


def test_dispatch_close_guard_requires_all_targets_and_second_sweep() -> None:
    dispatch = {
        "close_guard_policy": "all",
        "close_guard_targets": ["ollie", "flow"],
        "sweep_passes_required": 2,
    }
    assert governance_engine.dispatch_close_guard_satisfied(dispatch, {"ollie"}) is False
    assert governance_engine.dispatch_close_guard_satisfied(dispatch, {"flow"}) is False
    assert governance_engine.dispatch_close_guard_satisfied(dispatch, {"archie"}) is False
    assert governance_engine.dispatch_close_guard_satisfied(dispatch, {"ollie", "flow"}) is False
    assert governance_engine.dispatch_close_guard_satisfied(
        {**dispatch, "sweeps_completed": 2},
        {"ollie", "flow"},
    ) is True
