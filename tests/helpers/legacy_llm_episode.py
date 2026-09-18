"""Historical manual-loop fixtures only; shipped examples now use BenchmarkRunner.

Retains lower-level parsing/recording scenarios with an already-reset Environment.
It is not an Agent entry point and is not imported by the examples or platform.
"""
from __future__ import annotations
import json
import math
import time
from typing import Any, Callable
from examples.agent_integration import (
    DEFAULT_MODEL, llm_messages, _print_block, SUMMARY_DECISION_REQUEST,
    parse_model_reply, correction_message,
)
from sc2bench_env import Environment
from sc2bench_env.interface.observation_text import render_feedback_text

class EpisodeStopped(RuntimeError):
    def __init__(self, message: str, end_reason: str):
        super().__init__(message)
        self.end_reason = end_reason

def run_episode(
    env: Environment, call_llm: Callable, *, max_decisions: int = 200,
    max_consecutive_rejections: int = 3,
    max_api_attempts: int = 3, api_retry_delay_seconds: float = 1.0,
    verbose: bool = True,
    decision_summary: bool = True,
) -> dict[str, Any]:
    """reset 后调用；失败调用独立记录，调用者负责 finally close。"""
    if (type(max_api_attempts) is not int or max_api_attempts < 1
            or type(max_decisions) is not int or max_decisions < 1
            or type(max_consecutive_rejections) is not int or max_consecutive_rejections < 1
            or not math.isfinite(api_retry_delay_seconds) or api_retry_delay_seconds < 0):
        raise ValueError("API 尝试次数必须为正数，重试间隔不能为负数")
    rejected = 0
    correction = ""
    validation_errors = []
    for index in range(max_decisions):
        for attempt in range(1, max_api_attempts + 1):
            messages = llm_messages(env.get_context(), decision_summary=decision_summary)
            if correction:
                messages.append({"role": "user", "content": correction})
            if verbose:
                print(f"\n{'=' * 64}\n[SC2Bench] round={index + 1} "
                      f"API attempt={attempt}/{max_api_attempts}", flush=True)
                # These are the actual messages passed to call_llm, not a new snapshot.
                # Do not print the large fixed system prompt or gateway/key config.
                for message in messages:
                    if message.get("role") == "user":
                        _print_block("Agent input", message.get("content") or "(empty)")
                print("[SC2Bench] Waiting for model reply...", flush=True)
            call_started = time.perf_counter()
            try:
                response = call_llm(messages)
                if not isinstance(response, dict):
                    raise TypeError("LLM callback must return a response dictionary")
            except Exception as exc:
                # Never persist exception text: it may contain credentials/URLs.
                response = {"error": type(exc).__name__, "retryable": isinstance(exc, OSError),
                            "latency_seconds": time.perf_counter() - call_started}
            raw = response.get("raw_content", response.get("content", ""))
            context = {
                "messages": response.get("actual_messages", messages),
                "assistant_content": raw,
                "model": response.get("model", DEFAULT_MODEL),
                "usage": response.get("usage") or {},
                "reasoning": response.get("reasoning") or "",
                "finish_reason": response.get("finish_reason") or "",
                "thinking_requested": response.get("thinking_requested"),
                "latency_seconds": response.get("latency_seconds", 0),
                # Do not copy config, key, URL or request_metadata.
                "api_failed": bool(response.get("error")), "api_attempt": attempt,
                "decision_summary_requested": decision_summary,
            }
            if not response.get("error"):
                if verbose:
                    if context["reasoning"]:
                        _print_block("Model reasoning (API returned)", context["reasoning"])
                    elif context["thinking_requested"]:
                        print("[SC2Bench] thinking requested, but API returned no reasoning text.", flush=True)
                    _print_block("Model reply (raw)", raw or "(empty)")
                break
            safe_types = {"HTTPError", "URLError", "TimeoutError", "ConnectionError", "OSError",
                          "SSLError", "ValueError", "KeyError", "IndexError", "TypeError", "RuntimeError"}
            error_type = response.get("error")
            # Failed upstream replies may contain private diagnostic bodies.
            context["assistant_content"] = ""
            context["reasoning"] = ""
            context["usage"] = {}
            context["api_error_type"] = error_type if isinstance(error_type, str) and error_type in safe_types else "unknown"
            status = response.get("http_status")
            context["api_http_status"] = status if type(status) is int and 100 <= status <= 599 else None
            retryable = bool(response.get("retryable", True))
            context["api_retryable"] = retryable
            failure_info = env.record_agent_call_failure(context)
            if failure_info["terminated"]:
                return failure_info
            if attempt == max_api_attempts or not retryable:
                env.close(end_reason="agent_call_failed")
                raise EpisodeStopped("LLM 调用失败，已保存失败尝试；未提交动作，未计入非法决策", "agent_call_failed")
            print(f"API call failed: type={context['api_error_type']} attempt={attempt}/{max_api_attempts}; retrying", flush=True)
            if api_retry_delay_seconds:
                time.sleep(min(60.0, api_retry_delay_seconds * attempt))

        correction = ""
        context["json_repaired"] = False
        context["decision_summary"] = ""
        try:
            decision, context["json_repaired"], context["decision_summary"] = parse_model_reply(
                response.get("content") or "",
                decision_summary=decision_summary,
                # 已知被 token 上限截断的回复不补全后执行。
                allow_repair=response.get("finish_reason") != "length",
            )
        except (ValueError, TypeError, IndexError, RecursionError):
            decision = None
            correction = ("Your last reply could not be parsed into one final action array. "
                          + (SUMMARY_DECISION_REQUEST if decision_summary else
                             "Return only the action array, without Markdown fences or prose, ending with exactly one wait."))
        if verbose:
            if context["json_repaired"]:
                _print_block("Parsed decision (JSON repaired)",
                             json.dumps(decision, ensure_ascii=False, indent=2))
            print("[SC2Bench] Submitting decision and waiting for environment...", flush=True)
        observation, feedback, terminated, info = env.step(decision, agent_context=context)
        invalid = any(event.get("type") == "decision_rejected" for event in feedback.events)
        rejected = rejected + 1 if invalid else 0
        if not invalid:
            validation_errors.clear()
        print(f"round={index + 1} game_seconds={observation.game.game_time_seconds:.1f} "
              f"accepted={not invalid} json_repaired={context['json_repaired']} "
              f"terminated={terminated}", flush=True)
        if verbose:
            _print_block("Step feedback", render_feedback_text(feedback.to_dict()))
        for event in feedback.events:
            if event.get("type") == "decision_rejected":
                print(f"rejection_reason={event.get('reason', 'unknown')}", flush=True)
                reason = event.get("reason", "unknown")
                if reason not in validation_errors:
                    validation_errors.append(reason)
                # 把校验错误放到下一轮请求末尾；不改写动作、不放宽 Schema。
                correction = correction_message(validation_errors, decision_summary=decision_summary)
        if terminated:
            return info
        if rejected >= max_consecutive_rejections:
            raise EpisodeStopped("模型连续输出非法动作，停止示例；原始回复与拒绝原因已记录", "invalid_decision_limit")
    raise EpisodeStopped("已达到示例的模型调用轮数上限，对局将按中断保存", "decision_limit")
