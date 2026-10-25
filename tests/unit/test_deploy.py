from alembic.script import ScriptDirectory

from aiffel.db.deploy import _escape_literal, alembic_config


def test_migrations_are_found_without_alembic_ini() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    assert script.get_current_head() is not None


def test_password_literal_is_quoted() -> None:
    assert _escape_literal("a'b") == "'a''b'"
