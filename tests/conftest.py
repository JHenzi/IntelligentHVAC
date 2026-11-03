"""Shared fixtures."""
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """Load custom_components/intelligent_hvac with an in-memory recorder (a manifest dependency)."""
    yield
