"""Fixtures partagées : celles de ``aiffel.testing``, aussi utilisées par les extensions."""

import pytest

pytest.register_assert_rewrite("aiffel.testing")

from aiffel.testing import APP_PASSWORD, Database, create_farm  # noqa: E402

pytest_plugins = ["aiffel.testing"]

__all__ = ["APP_PASSWORD", "Database", "create_farm"]
