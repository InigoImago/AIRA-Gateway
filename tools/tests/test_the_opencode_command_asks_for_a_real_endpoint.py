"""The `/budget` command ships with the repository, so it must not go stale (`FRD-139` §5).

A command file is a **prompt**: OpenCode expands it into the session and the agent does the rest.
That makes two things checkable here and one thing not:

- the path it tells the agent to call has to be a route this gateway actually serves. A renamed
  endpoint would leave a file that asks an agent to curl a 404 and then describe the result, which
  is the worst of the three possible outcomes;
- it has to tell the agent to **refuse rather than invent**. Measured against the running stack
  with the only model this installation can serve (`qwen3:0.6b`): the first version of the file
  produced a confident answer in the template's own shape — *"there are no allowances"*, *"limits
  are not enforced"* — without ever calling the shell. Both statements were false. A budget figure
  somebody acts on must not be a model's paraphrase of the instructions for finding it.

What cannot be checked here is the agent actually running it: that needs a model able to use a
shell tool, and this installation serves a 0.6B one for test purposes. `FRD-139` §5 says so rather
than implying the file was exercised end to end.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "tools" / "opencode" / "command" / "budget.md"
ENDPOINT = ROOT / "gateway" / "src" / "aira_gateway" / "api" / "usage.py"


def _body() -> str:
    return COMMAND.read_text(encoding="utf-8")


def test_the_command_file_is_there_and_has_the_frontmatter_opencode_needs() -> None:
    """`description` is what the picker shows; without the delimiters the whole file is the prompt
    and the description leaks into it."""
    text = _body()

    assert text.startswith("---\n"), "no frontmatter, so the header would be part of the prompt"
    front, _, rest = text[4:].partition("\n---\n")
    assert "description:" in front, front
    assert rest.strip(), "no body, and the body is the prompt"


def test_it_names_a_route_this_gateway_serves() -> None:
    """The one thing a prompt file cannot discover for itself."""
    asked = set(re.findall(r"/v1beta/[a-z0-9/_-]+", _body()))
    served = set(re.findall(r'@router\.get\("(/v1beta/[^"]+)"\)', ENDPOINT.read_text()))

    assert asked, "the command asks for no endpoint at all"
    assert asked <= served, f"asks for {sorted(asked - served)}, which this surface does not serve"


def test_it_tells_the_agent_to_refuse_rather_than_invent() -> None:
    """The property the live run bought. A model that cannot run the command must say so."""
    text = _body().lower()

    assert "i could not read the budget" in text
    assert "made up" in text or "do not answer from anything other" in text


def test_it_sends_the_credential_the_agent_already_has() -> None:
    """The key is read from the environment, never written into a file in the repository — the one
    way a shipped recipe can leak a credential into version control."""
    text = _body()

    assert "$AIRA_API_KEY" in text
    assert "aira_" not in text.replace("$AIRA_API_KEY", ""), "a key-shaped literal is in the file"
