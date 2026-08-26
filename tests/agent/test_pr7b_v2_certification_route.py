from types import SimpleNamespace
from uuid import uuid4

from property_agent.agent.adapters.api.certification_router import prepare_v2_conversation
from property_agent.agent.runtime_version import AgentRuntimeVersion, RuntimeSelectionPolicy
from property_agent.main import create_app


def test_normal_production_composition_has_no_v2_certification_route(monkeypatch):
    import property_agent.main as main

    monkeypatch.setattr(main.settings, "deployment_environment", "production")
    monkeypatch.setattr(main.settings, "certification_write_enabled", False)
    app = create_app()
    assert "/api/certification/v2-conversations" not in app.openapi()["paths"]
    assert RuntimeSelectionPolicy().select_new() is AgentRuntimeVersion.V1


def test_preproduction_certification_route_is_conditionally_mounted(monkeypatch):
    import property_agent.main as main

    monkeypatch.setattr(main.settings, "deployment_environment", "preproduction")
    monkeypatch.setattr(main.settings, "certification_write_enabled", True)
    app = create_app()
    assert "/api/certification/v2-conversations" in app.openapi()["paths"]


def test_certification_preparation_persists_server_selected_v2():
    context = SimpleNamespace(current_house_id=uuid4())

    class Conversations:
        received = None

        def start(self, **kwargs):
            self.received = kwargs
            return SimpleNamespace(
                conversation_id=kwargs["conversation_id"],
                runtime_version=kwargs["runtime_version"],
            )

    conversations = Conversations()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(agent_conversations=conversations))
    )
    result = prepare_v2_conversation(request, context)
    assert result["runtime_version"] == "v2"
    assert conversations.received["runtime_version"] == "v2"
