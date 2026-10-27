"""Output validation only; no strategy decisions or inferred execution state."""
import json


def _unique_object(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            raise ValueError("duplicate_JSON_key")
        value[key] = child
    return value


def _invalid_constant(_value):
    raise ValueError("nonfinite_JSON_number")


def parse_json(text):
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)


def validate_plan(text, previous_plan=None):
    value = parse_json(text)
    if not isinstance(value, dict) or set(value) != {"plan", "completed_steps"}:
        raise ValueError("required_keys_are_plan_and_completed_steps")
    plan, completed = value["plan"], value["completed_steps"]
    if (not isinstance(plan, list) or not plan or not isinstance(completed, list)
            or any(not isinstance(s, str) or not s.strip() for s in plan + completed)):
        raise ValueError("plan_requires_nonempty_strings_and_completed_steps_requires_a_string_list")
    if len(set(plan)) != len(plan) or len(set(completed)) != len(completed):
        raise ValueError("duplicate_plan_steps")
    if previous_plan is None and completed:
        raise ValueError("initial_completed_steps_must_be_empty")
    if previous_plan is not None and not set(completed) <= set(previous_plan):
        raise ValueError("completed_steps_must_copy_exact_previous_plan_steps")
    if set(plan) & set(completed):
        raise ValueError("completed_steps_cannot_also_be_remaining_steps")
    return plan, completed
