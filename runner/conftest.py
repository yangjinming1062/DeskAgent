# Empty conftest so pytest auto-adds ``runner/`` to ``sys.path`` —
# lets ``import server`` resolve from ``runner/tests/`` without a
# ``[tool.pytest.ini_options] pythonpath = ["."]`` special case in
# pyproject.toml.
