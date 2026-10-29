"""Zero-shot ReAct: reason or act, append observations, and continue."""
from copy import deepcopy

from agents.llm_agent.agent import LLMAgent, InvalidDecisionLimit, _model_call, _print_block
from agents.llm_agent.client import make_llm_call
from agents.llm_agent.config import llm_api_key, llm_base_url, llm_model
from agents.llm_agent.tools import tool_schemas
from sc2bench_env.adapters.llm import LLMAdapter
from sc2bench_env.interface.agent import AgentInput, AgentStopped, AgentTurn
from sc2bench_env.interface.tools import ToolCall, ToolResult

from .prompts import NO_TOOLS, react_prompt_parts


class ReActAgent(LLMAgent):
    """Reuse transport/recording; allow interleaved tools and advance last."""

    def __call__(self, request: AgentInput) -> AgentTurn:
        self._append_turn_input(request)
        if request.feedback is not None:
            rejected = any(event.get("type") == "decision_rejected"
                           for event in request.feedback.events)
            self.rejected = self.rejected + 1 if rejected else 0
            if self.rejected >= self.max_consecutive_rejections:
                raise InvalidDecisionLimit()
        if self.verbose:
            _print_block("Agent input", self.messages[-1]["content"])

        race = str(request.observation.game.race or "terran").strip().lower()
        adapter = LLMAdapter(race=race)
        schemas = tool_schemas(request.tool_specs)
        self._sent_tools = schemas
        failures, decision_calls = [], []
        response, attempt = {}, 1

        def context():
            result = self._turn_context(response, request, attempt, failures)
            result["agent_calls"] = list(decision_calls)
            return result

        for _round in range(self.max_tool_rounds):
            response, attempt, round_failures = self._complete_once(schemas)
            failures.extend(round_failures)
            if response.get("error"):
                return AgentTurn(context(), tuple(failures), True)
            decision_calls.append(_model_call(response))
            content = response.get("content") or ""
            calls = response.get("tool_calls") or []
            if not isinstance(calls, list) or any(not isinstance(c, dict) for c in calls):
                self.messages.append({"role": "assistant", "content": content})
                self.messages.append({"role": "user", "content": "The tool-call envelope is invalid."})
                raise AgentStopped("agent_stopped", agent_context=context())
            calls = [dict(c, id=str(c.get("id") or f"call_{len(self.messages)}_{i}"))
                     for i, c in enumerate(calls)]
            if len({c["id"] for c in calls}) != len(calls):
                self.messages.append({"role": "assistant", "content": content})
                self.messages.append({"role": "user", "content": "Tool-call IDs must be unique."})
                raise AgentStopped("agent_stopped", agent_context=context())
            assistant = self._assistant_tool_message(content, calls)
            if not calls:
                assistant["content"] = content
            self.messages.append(assistant)
            if self.verbose and content:
                _print_block("Model note", content)
            if not calls:
                # In the author's ALFWorld loop, a think: step receives OK.
                # Here plain assistant text is the reasoning step; no game
                # tool or time advance occurs. Native reasoning is retained too.
                reasoning = response.get("reasoning") or ""
                if content.strip() or (isinstance(reasoning, str) and reasoning.strip()):
                    self.messages.append({"role": "user", "content": "OK."})
                else:
                    self.messages.append({"role": "user", "content": NO_TOOLS})
                continue

            advances = [i for i, c in enumerate(calls) if c.get("name") == "advance"]
            problem = None
            if response.get("finish_reason") == "length":
                problem = "The reply was truncated. None of these calls was submitted. Submit a complete tool reply."
            elif advances and advances != [len(calls) - 1]:
                problem = "None of these calls was submitted. Call advance only once, as the final call in a reply."
            elif any(not isinstance(c.get("arguments"), dict) for c in calls):
                problem = "None of these calls was submitted. Tool arguments must be JSON objects."
            if problem:
                self._reject_tool_reply(calls, problem)
                self.messages.append({"role": "user", "content": problem})
                continue

            for index, call in enumerate(calls):
                tool_call = ToolCall(str(call.get("name") or ""), deepcopy(call["arguments"]))
                try:
                    result = request.call_tool(tool_call)
                    if not isinstance(result, ToolResult):
                        raise TypeError("invalid tool result")
                    rendered = adapter.render_tool_result(tool_call, result)
                except Exception as error:
                    # The callback may have applied the action before failing.
                    # Stop rather than replaying an action of unknown outcome.
                    self.messages.append({
                        "role": "tool", "tool_call_id": call["id"],
                        "content": f"status: execution_unknown\nerror_type: {type(error).__name__}",
                    })
                    self._reject_tool_reply(calls[index + 1:], "Not submitted after a tool execution error.")
                    raise AgentStopped("agent_stopped", agent_context=context()) from error
                self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": rendered})
                if self.verbose:
                    _print_block("Tool result", rendered)
                if result.status == "decision_ready":
                    if tool_call.name != "advance":
                        self._reject_tool_reply(calls[index + 1:], "Unexpected decision boundary; remaining calls were not submitted.")
                        raise AgentStopped("agent_stopped", agent_context=context())
                    return AgentTurn(context(), tuple(failures))
        raise AgentStopped("tool_round_limit", agent_context=context())

    def _append_turn_input(self, request: AgentInput) -> None:
        fresh = not self.messages or self.conversation_mode == "per_decision"
        super()._append_turn_input(request)
        if fresh:
            race = str(request.observation.game.race or "terran").strip().lower()
            content = LLMAdapter(react_prompt_parts(race), race=race).system_prompt().rstrip()
            if self.skill_text:
                content += f"\n\n[Agent Skill: {self.skill_name}]\n{self.skill_text}\n"
            self.messages[0] = {"role": "system", "content": content}

    def _complete_once(self, schemas):
        self._save_pending_reasoning()
        response, attempt, failures = super()._complete_once(schemas)
        reasoning = response.get("reasoning")
        if not response.get("error") and (self.thinking_requested or reasoning):
            reasoning = reasoning if isinstance(reasoning, str) else ""
            # The shared loop appends the assistant message after this returns.
            # Save the provider field before the next request or final snapshot.
            self._pending_reasoning = (len(self.messages), reasoning)
            if self.verbose and reasoning:
                _print_block("Model reasoning", reasoning)
        return response, attempt, failures

    def _save_pending_reasoning(self):
        pending = getattr(self, "_pending_reasoning", None)
        if pending is not None:
            index, reasoning = pending
            if index < len(self.messages) and self.messages[index].get("role") == "assistant":
                self.messages[index]["reasoning_content"] = reasoning
            self._pending_reasoning = None

    def _turn_context(self, response, request, attempt, failures):
        self._save_pending_reasoning()
        return super()._turn_context(response, request, attempt, failures)


def create_agent(call_llm=None, **agent_options) -> ReActAgent:
    """Create an isolated episode; accept the same options as LLMAgent."""
    if call_llm is None:
        call_llm = make_llm_call(
            api_key=llm_api_key(), base_url=llm_base_url(), model=llm_model(),
            thinking=bool(agent_options.get("thinking_requested", False)),
        )
    return ReActAgent(call_llm, **agent_options)
