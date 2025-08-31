import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import usernamechecker


@pytest.mark.asyncio
async def test_pretty_print_handles_run_errors(monkeypatch, capsys):
    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(usernamechecker, "check_one", boom)
    monkeypatch.setattr(usernamechecker, "SITES", {
        "dummy": {"url": "https://example.com/{username}"}
    })

    data = await usernamechecker.run("user", ["dummy"], concurrency=1, timeout=1, retries=0)
    res = data["results"][0]
    assert res["site"] == "dummy"
    assert res["error"] == "boom"

    usernamechecker.pretty_print(data)
    out = capsys.readouterr().out
    assert "boom" in out or "ERREUR" in out
