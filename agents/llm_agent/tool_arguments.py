"""Bounded, schema-guided repair of transport encoding, never action intent."""
from __future__ import annotations

from copy import deepcopy
import json
import re


def _without_trailing_commas(text):
    result, quoted, escaped = [], False, False
    for i, char in enumerate(text):
        if char == '"' and not escaped:
            quoted = not quoted
        if char == ',' and not quoted and text[i + 1:].lstrip().startswith(('}', ']')):
            continue
        result.append(char)
        escaped = char == '\\' and not escaped if quoted else False
    return ''.join(result)


def _decode(value):
    """Decode full JSON values only; never scan prose for possible actions."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    fence = re.fullmatch(r'```(?:json)?\s*\n?(.*?)\s*```', text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence[1]
    for candidate in (text, _without_trailing_commas(text)):
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            pass
    # Some XML parsers leave a JSON container with escaped quotation marks.
    if text.startswith(('[', '{')) and '\\"' in text:
        try:
            unescaped = json.loads('"' + text + '"')
            return json.loads(unescaped)
        except (ValueError, TypeError):
            pass
    return value


def _container_types(schema):
    kind = schema.get('type')
    kinds = {kind} if isinstance(kind, str) else set(kind or [])
    for branch in schema.get('oneOf', []) + schema.get('anyOf', []):
        kinds.update(_container_types(branch))
    return kinds & {'array', 'object'}


def repair_arguments(value, schema):
    """Return repaired JSON plus paths changed, keeping scalars and names exact."""
    original = deepcopy(value)
    paths = []

    def visit(current, spec, path):
        starting = current
        containers = _container_types(spec)
        for _ in range(3):
            if not isinstance(current, str) or not containers:
                break
            decoded = _decode(current)
            if decoded == current:
                break
            if isinstance(decoded, str):
                # Nested encoding is accepted only when it eventually becomes
                # a container of the schema's type below.
                current = decoded
                continue
            if ((isinstance(decoded, dict) and 'object' in containers)
                    or (isinstance(decoded, list) and 'array' in containers)):
                current = decoded
            break
        if not isinstance(current, (dict, list)):
            current = starting
        if isinstance(current, dict):
            properties = spec.get('properties') or {}
            return {key: visit(item, properties.get(key, {}), path + '.' + key)
                    for key, item in current.items()}
        if isinstance(current, list) and 'array' in containers:
            item_schema = spec.get('items') or next(
                (b.get('items', {}) for b in spec.get('oneOf', []) if b.get('type') == 'array'), {})
            return [visit(item, item_schema, path + '[' + str(i) + ']') for i, item in enumerate(current)]
        return current

    fixed = visit(value, schema, '$')
    # A partially decoded string is not a repair of a typed container.
    if not isinstance(fixed, dict):
        return original, []

    def changes(before, after, path):
        if type(before) is not type(after):
            paths.append(path)
        elif isinstance(before, dict):
            for key in before:
                changes(before[key], after[key], path + '.' + key)
        elif isinstance(before, list):
            for i, (left, right) in enumerate(zip(before, after)):
                changes(left, right, path + '[' + str(i) + ']')
        elif before != after:
            paths.append(path)

    # The normal outer JSON object supplied by Chat Completions is transport,
    # not itself a repair. The caller distinguishes that initial decode.
    changes(original, fixed, '$')
    return fixed, paths
