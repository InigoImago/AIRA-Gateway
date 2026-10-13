"""What a model's name may be, in one place both planes read (`FRD-114`, `ADR-0011`).

A model's name is its **identity**: Management writes it, it crosses Kafka into the gateway's
catalogue, keys every release, pipeline step and audit row, and comes back in URL path segments and
file names. So the rule is the gateway's lookup rule — a name the gateway could never look up is a
model that is approved and can never be served — stated once, so the plane that writes a name and
the plane that looks it up cannot disagree about which names exist.

A `/` is deliberately allowed: it is the ordinary form on an OpenAI-compatible server
(`meta-llama/Llama-3.1-8B-Instruct`), such a model is served through the KIRA surface's numeric id,
and Management's routes address it whole.
"""

from __future__ import annotations

#: The widest name the catalogue holds: `catalog_model.name`, `model_catalog.model` and
#: `request_logs.model` are all 128 characters.
MAX_MODEL_NAME = 128


def is_control(character: str) -> bool:
    """A C0 control character or DEL — never part of a name a person or a vendor writes."""
    return ord(character) < 0x20 or ord(character) == 0x7F


def is_lookupable(name: str) -> bool:
    """Whether ``name`` is a name the catalogue could hold.

    Empty, longer than :data:`MAX_MODEL_NAME`, or carrying a control character: a NUL makes Postgres
    raise, an over-long name cannot be stored beside a request, and a line break in a name ends up
    in a response header.
    """
    return bool(name) and len(name) <= MAX_MODEL_NAME and not any(map(is_control, name))


def name_problem(name: str) -> str | None:
    """Why ``name`` cannot be a model's name, as a sentence for whoever typed it, or ``None``."""
    if not name:
        return "A model needs a name."
    if len(name) > MAX_MODEL_NAME:
        return f"A model's name is at most {MAX_MODEL_NAME} characters."
    if any(map(is_control, name)):
        return (
            "A model's name cannot contain a control character such as a line break or a tab: "
            "the gateway could never look it up, and the name is printed into headers and files."
        )
    return None
