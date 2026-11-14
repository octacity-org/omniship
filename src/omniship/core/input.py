"""Runtime workflow-input references and recursive value resolution."""

import json
from collections.abc import Mapping
from typing import Any

from omniship.core.execution import REF_NAME_ENV, REF_TYPE_ENV

INPUT_REFERENCE_KEY = "$omniship_input"
RUNTIME_INPUTS_ENV = "OMNISHIP_INPUTS"
# Marks a value whose strings contain {tag} or {version}, filled in from the
# pushed tag when the task runs.
REF_TEMPLATE_KEY = "$omniship_ref_template"
_REF_PLACEHOLDERS = ("{tag}", "{version}")


class RuntimeInputError(ValueError):
    """Raised when runtime workflow inputs are missing or malformed."""

    pass


def runtime_input_reference(name: str) -> dict[str, str]:
    """Create the serializable marker used to defer an input until execution."""

    return {INPUT_REFERENCE_KEY: name}


def resolve_runtime_inputs(value: Any, inputs: Mapping[str, Any]) -> Any:
    """Recursively replace input markers while preserving container types.

    A mapping is treated as a reference only when its sole key is
    :data:`INPUT_REFERENCE_KEY`; ordinary mappings are traversed normally.
    """

    if isinstance(value, dict):
        if set(value) == {INPUT_REFERENCE_KEY}:
            name = value[INPUT_REFERENCE_KEY]
            if not isinstance(name, str) or not name:
                raise RuntimeInputError("Runtime input reference has an invalid name")
            if name not in inputs:
                raise RuntimeInputError(
                    f"Required workflow input '{name}' was not provided"
                )
            return inputs[name]
        return {
            key: resolve_runtime_inputs(item, inputs) for key, item in value.items()
        }
    if isinstance(value, list):
        return [resolve_runtime_inputs(item, inputs) for item in value]
    if isinstance(value, tuple):
        return tuple(resolve_runtime_inputs(item, inputs) for item in value)
    return value


def ref_template(value: Any) -> Any:
    """Defer ``{tag}`` and ``{version}`` in ``value`` until the task runs.

    ``value`` is a string or a list or mapping of strings; mapping keys are
    filled in too. Values without either placeholder are returned unchanged,
    so existing configurations compile the same.
    """

    return {REF_TEMPLATE_KEY: value} if _has_ref_placeholder(value) else value


def resolve_ref_templates(value: Any, env: Mapping[str, str]) -> Any:
    """Fill in every :func:`ref_template` value from the pushed tag in ``env``.

    ``{tag}`` is the tag name, such as ``v1.2.0``, and ``{version}`` is the tag
    without a leading ``v``. Outside a tag run a template raises
    :class:`RuntimeInputError`.
    """

    if isinstance(value, dict):
        if set(value) == {REF_TEMPLATE_KEY}:
            return _fill_ref(value[REF_TEMPLATE_KEY], _pushed_tag(env))
        return {key: resolve_ref_templates(item, env) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_ref_templates(item, env) for item in value]
    if isinstance(value, tuple):
        return tuple(resolve_ref_templates(item, env) for item in value)
    return value


def _has_ref_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return any(placeholder in value for placeholder in _REF_PLACEHOLDERS)
    if isinstance(value, Mapping):
        return any(
            _has_ref_placeholder(key) or _has_ref_placeholder(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_has_ref_placeholder(item) for item in value)
    return False


def _pushed_tag(env: Mapping[str, str]) -> str:
    tag = env.get(REF_NAME_ENV)
    if env.get(REF_TYPE_ENV) != "tag" or not tag:
        raise RuntimeInputError(
            "{tag} and {version} name the pushed tag, but this run was not "
            "started by a tag push"
        )
    return tag


def _fill_ref(value: Any, tag: str) -> Any:
    if isinstance(value, str):
        return value.replace("{tag}", tag).replace("{version}", tag.removeprefix("v"))
    if isinstance(value, Mapping):
        return {
            _fill_ref(key, tag): _fill_ref(item, tag) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(_fill_ref(item, tag) for item in value)
    return value


def load_runtime_inputs(env: Mapping[str, str]) -> dict[str, Any]:
    """Load workflow inputs from the JSON object in ``OMNISHIP_INPUTS``."""

    raw = env.get(RUNTIME_INPUTS_ENV)
    if not raw:
        return {}
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeInputError(
            f"{RUNTIME_INPUTS_ENV} must contain valid JSON"
        ) from exc
    if not isinstance(values, dict):
        raise RuntimeInputError(f"{RUNTIME_INPUTS_ENV} must contain a JSON object")
    return values
