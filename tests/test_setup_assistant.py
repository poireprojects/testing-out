import json
import sys

import httpx
import pytest

from dkapp import setup_assistant as sa


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://localhost:11434")


def test_status_detects_running_ollama_and_models():
    c = client(lambda r: httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]}))
    st = sa.status("http://localhost:11434", c)
    assert st.running and st.installed and st.has("llama3.1:8b") and not st.has("llama3.2:3b")


def test_status_when_ollama_is_not_running():
    def down(r):
        raise httpx.ConnectError("refused")
    st = sa.status("http://localhost:11434", client(down))
    assert not st.running and st.models == []


def test_recommended_model_depends_on_memory():
    assert sa.recommended_model(32)[0] == "llama3.1:8b"
    assert sa.recommended_model(8)[0] == "llama3.2:3b"


def test_pull_model_streams_progress_until_success():
    lines = [{"status": "pulling manifest"}, {"status": "downloading", "total": 1000, "completed": 250},
             {"status": "downloading", "total": 1000, "completed": 1000}, {"status": "verifying"},
             {"status": "success"}]
    seen = {}

    def handler(r):
        seen["body"] = json.loads(r.content)
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())
    updates = []
    sa.pull_model("http://localhost:11434", "llama3.2:3b", lambda f, m: updates.append((f, m)), client(handler))
    assert seen["body"] == {"model": "llama3.2:3b", "stream": True}
    assert (0.25, ) == (updates[1][0],) and updates[-1] == (1.0, "llama3.2:3b is ready")


def test_pull_model_reports_errors():
    c = client(lambda r: httpx.Response(200, content=json.dumps({"error": "pull model manifest: not found"}).encode()))
    with pytest.raises(RuntimeError, match="not found"):
        sa.pull_model("http://localhost:11434", "nope", lambda f, m: None, c)


@pytest.mark.skipif(sys.platform in sa.OLLAMA_DOWNLOADS, reason="auto-install supported here")
def test_install_explains_when_unsupported():
    with pytest.raises(RuntimeError, match="ollama.com"):
        sa.install(lambda f, m: None)
