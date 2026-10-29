"""Zero-shot native-tool adaptation of the author's ReAct interaction loop."""
from dataclasses import replace

from sc2bench_env.interface.platform_prompt import default_prompt_parts


REACT_GUIDANCE = (
    "Interact with StarCraft II to achieve the objective described above. "
    "Reason about the current observation and interaction history when deciding what to do next. "
    "You may reason before acting, or act directly when the next action is clear. "
    "Write any reasoning in message content and use native tool calls for actions. "
    "Use the returned observations to update your understanding and choose the next action. "
    "Observations come from the environment; do not generate them yourself."
)
NO_TOOLS = (
    "The reply contained neither analysis nor a tool call. "
    "Continue from the current observation."
)


def react_prompt_parts(race: str):
    return replace(
        default_prompt_parts(race),
        decision_guidance=REACT_GUIDANCE,
    )
