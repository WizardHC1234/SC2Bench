r"""完整 LLM Agent + BenchmarkRunner 接入示例（单局）。

在已安装平台/实机依赖的环境中运行：
    python examples/agent_integration.py --dry-run
    python examples/agent_integration.py --opponent easy
    python examples/agent_integration.py --skill examples/skills/tank.md --difficulty medium
    python examples/run_llm_benchmark.py --repetitions 1

首次接入先看 main() 的“平台接入”部分，再看 LLMAgent.__call__。
平台契约只有：AgentInput → 你的Agent → 动作数组或AgentTurn。
本文件较长是因为包含完整外部模型客户端/解析/重试，不是平台要求的代码量。
需要定制模型或Harness时，再读顶部配置、API客户端、解析与build_messages。
正常运行调用模型并启动SC2；--dry-run/--help 不调用API/游戏或创建记录。
本例自己组织上下文、调用API并解析动作，Runner 驱动每轮决策、执行、
终局、清理、记录和结果汇总；不另写 reset/step 对局循环。
默认阻塞决策、最多60游戏秒返回，API thinking 默认关闭；
默认要求简短决策摘要后输出动作数组，--no-decision-summary 保留JSON-only。
默认打印模型输入/原始回复，--quiet 收起全文。
本例无默认 Skill、规则打法或长期 Memory；--skill 显式加载外部策略文件。
密钥在顶部 API_KEY 或 LLM_API_KEY 环境变量配置，分享前去除真实密钥；
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import time
from http.client import HTTPException
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from sc2bench_env.interface.platform_rules import DECISION_OUTPUT_RULE, OUTPUT_FORMAT_RULE
from sc2bench_env.interface.opponents import (
    BUILTIN_OPPONENTS, ENEMY_STYLES, normalize_opponent, normalize_enemy_style,
)
from sc2bench_env.interface.races import ENEMY_RACES


# 外部模型配置/客户端：由Agent维护，不属于环境接口。
# 当前默认：DeepSeek-V4-Flash。
DEFAULT_MODEL = "DeepSeek-V4-Flash"
DEFAULT_API_BASE_URL = "http://172.18.132.20:2325/v1"
DEFAULT_TEMPERATURE = 0.5
API_KEY = "EMPTY"  # 本地示例密钥；勿提交或分享此文件。

# Only reviewed class names are logged; never persist exception text/HTTP bodies.
SAFE_API_ERROR_TYPES = frozenset({
    "HTTPError", "URLError", "TimeoutError", "ConnectionError", "OSError",
    "SSLError", "RemoteDisconnected", "ConnectionResetError",
    "ConnectionAbortedError", "BrokenPipeError", "IncompleteRead",
    "BadStatusLine", "HTTPException", "JSONDecodeError", "UnicodeDecodeError",
    "ValueError", "KeyError", "IndexError", "TypeError", "RuntimeError",
})

DEFAULT_SUITE = Path(__file__).resolve().parents[1] / "benchmarks" / "terran_pilot.json"


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _finite_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a finite number") from error
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError("must be a finite number")
    return number


def positive_float(value: str) -> float:
    number = _finite_float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def nonnegative_float(value: str) -> float:
    number = _finite_float(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def parse_temperature(value: str) -> float:
    number = _finite_float(value)
    if not 0 <= number <= 2:
        raise argparse.ArgumentTypeError("temperature must be between 0 and 2")
    return number


def print_batch_result(batch: dict[str, Any]) -> int:
    """Traversal completion is not proof that every episode completed."""
    print(f"Batch: {batch['summary_path']}", flush=True)
    for row in batch["episodes"]:
        print(
            f"{row['index']}. {row['config']['opponent']}: {row['status']}/"
            f"{row['outcome']} ({row['end_reason']}), "
            f"decisions={row['decision_count']}, rejected={row['rejected_count']}",
            flush=True,
        )
    print(f"Aggregate: {batch['aggregate']}", flush=True)
    print(f"Termination counts: {batch['termination_counts']}", flush=True)
    return 0 if all(row["status"] == "completed" for row in batch["episodes"]) else 1



def repair_json(*args, **kwargs):
    """Load the optional parser only when needed; --help works with core only."""
    from json_repair import repair_json as repair
    return repair(*args, **kwargs)


def resolve_api_key() -> str:
    """环境变量优先，否则使用本文件顶部的本地密钥。"""
    return (os.environ.get("LLM_API_KEY") or "").strip() or API_KEY.strip()


def split_reasoning(text: str) -> tuple[str, str]:
    """解析网关的 think 包裹；不修补 Markdown 或非法动作 JSON。"""
    pattern = r"<think\b[^>]*>(.*?)</think>"
    thoughts = re.findall(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(pattern, "", text, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"<think\b[^>]*>.*", "", content, flags=re.IGNORECASE | re.DOTALL)
    return content.strip(), "\n\n".join(thought.strip() for thought in thoughts)


def parse_decision(text: str, *, allow_repair: bool = True) -> tuple[Any, bool]:
    """外部 Agent 格式适配；平台仍负责动作含义和字段校验。"""
    try:
        return json.loads(text), False
    except json.JSONDecodeError:
        if not allow_repair:
            raise
    decision = repair_json(text, return_objects=True, skip_json_loads=True)
    # 不把空回复/普通文本修复出来的空值当成有效决策。
    if not isinstance(decision, list) or not decision:
        raise ValueError("reply could not be repaired into a non-empty action array")
    return decision, True


SUMMARY_OUTPUT_RULE = """Output: First write one concise decision-analysis paragraph (2-4 sentences) explaining the current situation, your immediate objective, and the grounds for the commands you choose. Then leave a blank line and output exactly one JSON action array with new commands and exactly one final wait. No Markdown fences, wrapper object or extra keys. The paragraph is required; choose your own strategy."""
SUMMARY_DECISION_REQUEST = "First write one concise decision-analysis paragraph, then a blank line and exactly one NEW-command JSON action array ending with one wait. Use only each action's listed fields; no Markdown fences or text after the array."


def correction_message(reasons: list[str], *, decision_summary: bool) -> str:
    """One format-only hint for single and batch runs; never invent a strategy."""
    return (
        "Your last action array was rejected and was not applied. "
        "Validation error: " + "; ".join(reasons) + ". "
        "Return a corrected full action array using only the fields and "
        "targets in the platform instructions, ending with exactly one wait. "
        "Zone targets and scout route items must be quoted zone_<index> strings copied exactly from Observation, not numeric indices. "
        "combat.group and retreat.group must be quoted group_<index> strings copied exactly from an existing outbound group in Observation, not numeric indices; group_0 is not an outbound group. "
        "Timed waits use any_of/all_of arrays of condition objects. "
        "interval is a condition with seconds, not a wait field. "
        "seconds belongs inside the interval condition object, never directly on wait. "
        + (SUMMARY_DECISION_REQUEST if decision_summary else "Do not use Markdown fences or prose.")
    )


def llm_messages(messages: list[dict[str, str]], *, decision_summary: bool) -> list[dict[str, str]]:
    """Adapt example output instructions without mutating environment context."""
    adapted = [dict(message) for message in messages]
    if not decision_summary:
        return adapted
    replacements = (
        (OUTPUT_FORMAT_RULE, SUMMARY_OUTPUT_RULE),
        (DECISION_OUTPUT_RULE, SUMMARY_DECISION_REQUEST),
        ('With no new commands, return [{"action":"wait"}].',
         'With no new commands, the action array is [{"action":"wait"}].'),
        ("Complete reply shape:", "Action-array shape (after the summary paragraph):"),
    )
    for original, replacement in replacements:
        matches = sum(message["content"].count(original) for message in adapted)
        if matches != 1:
            raise ValueError("Example summary adapter does not match platform output instructions")
        for message in adapted:
            message["content"] = message["content"].replace(original, replacement)
    return adapted


def split_decision_summary(text: str) -> tuple[str, str]:
    """Extract one final array; never greedily combine separate action arrays."""
    candidate = re.search(r"(?m)^[ \t]*\[(?=\s*(?:\{|$))", text)
    if candidate is None:
        raise ValueError("No final action array found")
    prefix = text[:candidate.start()].strip()
    prefix = re.sub(r"(?:^|\n)```(?:json)?\s*$", "", prefix, flags=re.IGNORECASE).strip()
    if prefix.startswith(("[", "{")):
        raise ValueError("A JSON wrapper or earlier array is not a decision summary")
    action_text = text[candidate.start():].strip()
    action_text = re.sub(r"\n?```\s*$", "", action_text).strip()
    depth, quote, escaped = 0, "", False
    for index, char in enumerate(action_text):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in ('"', "'"):
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                if action_text[index + 1:].strip():
                    raise ValueError("Multiple arrays or trailing text are not supported")
                # Preserve syntax-repair reporting for pure/fenced JSON replies.
                return prefix, action_text if prefix else text
    raise ValueError("Incomplete final action array")


def parse_model_reply(text: str, *, decision_summary: bool, allow_repair: bool = True) -> tuple[Any, bool, str]:
    summary, action_text = split_decision_summary(text) if decision_summary else ("", text)
    decision, repaired = parse_decision(action_text, allow_repair=allow_repair)
    return decision, repaired, summary


def make_llm_call(
    *, api_key: str, base_url: str = DEFAULT_API_BASE_URL,
    model: str = DEFAULT_MODEL, timeout: float = 120,
    temperature: float = DEFAULT_TEMPERATURE, thinking: bool = False,
) -> Callable:
    if not api_key.strip():
        raise ValueError("请填写顶部 API_KEY 或设置 LLM_API_KEY 环境变量")
    if not math.isfinite(timeout) or timeout <= 0 or not math.isfinite(temperature) or not 0 <= temperature <= 2:
        raise ValueError("API timeout must be positive/finite; temperature must be finite and between 0 and 2")
    if not model.strip():
        raise ValueError("model must not be empty")
    base_url = base_url.strip().rstrip("/")
    if base_url.endswith("/chat/completions"):
        endpoint = base_url
    else:
        endpoint = base_url + "/chat/completions"
    parsed_url = urlsplit(endpoint)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise ValueError("API 网关必须是 http:// 或 https:// 地址")
    if parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment:
        raise ValueError("API gateway must not contain URL credentials, query or fragment")

    def call(messages: list[dict[str, str]]) -> dict[str, Any]:
        # Kimi uses thinking.type; DeepSeek-style gateways use chat_template_kwargs.
        model_l = model.lower()
        if "kimi" in model_l:
            payload = {
                "model": model, "messages": messages, "temperature": temperature,
                "stream": False,
                "thinking": {"type": "enabled" if thinking else "disabled"},
                "chat_template_kwargs": {"thinking": bool(thinking)},
                "response_format": {"type": "text"},
            }
        else:
            payload = {
                "model": model, "messages": messages, "temperature": temperature,
                "stream": False,
                "chat_template_kwargs": {
                    "thinking": thinking, "enable_thinking": thinking,
                },
                "response_format": {"type": "text"},
            }
        request = Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          headers={"Authorization": f"Bearer {api_key}",
                                   "Content-Type": "application/json"}, method="POST")
        started = time.perf_counter()
        try:
            with urlopen(request, timeout=timeout) as http_response:
                completion = json.loads(http_response.read().decode("utf-8"))
            choice = completion["choices"][0]
            message = choice["message"]
            raw = message.get("content") or ""
            if not isinstance(raw, str):
                raise ValueError("message.content is not text")
            content, thoughts = split_reasoning(raw)
            return {
                "content": content, "raw_content": raw,
                "reasoning": message.get("reasoning_content") or message.get("reasoning") or thoughts,
                "model": completion.get("model") or model,
                "usage": completion.get("usage") or {},
                "finish_reason": choice.get("finish_reason") or "",
                "thinking_requested": thinking,
                "latency_seconds": round(time.perf_counter() - started, 6),
                "actual_messages": messages, "error": "",
            }
        except (URLError, OSError, HTTPException, ValueError, KeyError, IndexError, TypeError) as exc:
            status = exc.code if isinstance(exc, HTTPError) else None
            retryable = (status in {408, 429} or status >= 500) if status is not None else isinstance(exc, (URLError, OSError, HTTPException))
            return {"content": "", "raw_content": "", "model": model,
                    "thinking_requested": thinking,
                    "actual_messages": messages, "error": type(exc).__name__,
                    "http_status": status, "retryable": retryable,
                    "latency_seconds": round(time.perf_counter() - started, 6)}

    return call


def _print_block(title: str, content: str) -> None:
    """Keep console views separate without changing the actual API/record data."""
    print(f"\n[{title}]\n{content}", flush=True)



from sc2bench_env.benchmark import AgentInput, AgentStopped, AgentTurn, BenchmarkRunner
from sc2bench_env.interface.config import EpisodeConfig

class InvalidDecisionLimit(AgentStopped):
    """The external model reached its consecutive rejection limit."""

    def __init__(self, message: str = "consecutive invalid decisions") -> None:
        super().__init__("invalid_decision_limit")


class LLMAgent:
    """单局外部 Agent：接收 AgentInput，调用模型，返回 AgentTurn。

    单局与多局统一由 Runner 调用。Runner 负责 Environment
    生命周期；这里负责模型请求、有限重试、回复解析及真实交互数据。
    实例只用于一局，上一局的拒绝记录不会残留到下一局。
    """

    def __init__(
        self, call_llm: Callable[[list[dict[str, str]]], dict[str, Any]],
        *, decision_summary: bool = True, max_api_attempts: int = 3,
        api_retry_delay_seconds: float = 1.0,
        max_consecutive_rejections: int = 3,
        thinking_requested: bool = False,
        verbose: bool = True,
        skill_text: str = "",
    ) -> None:
        if (type(max_api_attempts) is not int or max_api_attempts < 1
                or not math.isfinite(api_retry_delay_seconds) or api_retry_delay_seconds < 0
                or type(max_consecutive_rejections) is not int or max_consecutive_rejections < 1):
            raise ValueError("invalid external Agent retry or rejection limits")
        self.call_llm = call_llm
        self.decision_summary = decision_summary
        self.max_api_attempts = max_api_attempts
        self.api_retry_delay_seconds = api_retry_delay_seconds
        self.max_consecutive_rejections = max_consecutive_rejections
        self.thinking_requested = thinking_requested
        self.verbose = verbose
        self.skill_text = skill_text.strip()
        self.rejected = 0
        self.validation_errors: list[str] = []

    def __call__(self, request: AgentInput) -> AgentTurn:
        # 平台输入：platform_messages已包含规则、当前Obs和上次回执。
        # feedback用于检查提交结果；当前执行进度以observation为准。
        correction = ""
        if request.feedback is not None:
            reasons = [str(event.get("reason", "unknown"))
                       for event in request.feedback.events
                       if event.get("type") == "decision_rejected"]
            if reasons:
                self.rejected += 1
                if self.rejected >= self.max_consecutive_rejections:
                    raise InvalidDecisionLimit("consecutive invalid decisions")
                for reason in reasons:
                    if reason not in self.validation_errors:
                        self.validation_errors.append(reason)
                correction = correction_message(self.validation_errors, decision_summary=self.decision_summary)
            else:
                self.rejected = 0
                self.validation_errors.clear()

        messages = self.build_messages(request, correction)
        if self.verbose:
            for message in messages:
                if message.get("role") == "user":
                    _print_block("Agent input", message["content"])

        response: dict[str, Any] = {}
        failures: list[dict[str, Any]] = []
        for attempt in range(1, self.max_api_attempts + 1):
            started = time.perf_counter()
            try:
                response = self.call_llm(messages)
                if not isinstance(response, dict):
                    raise TypeError("LLM callback must return a response dictionary")
            except Exception as error:
                # The error text may contain credentials or endpoint details.
                response = {"error": type(error).__name__,
                            "retryable": isinstance(error, (OSError, HTTPException)),
                            "latency_seconds": time.perf_counter() - started}
            if not response.get("error"):
                break
            error_type = response.get("error")
            status = response.get("http_status")
            failures.append({
                "messages": messages, "assistant_content": "",
                "model": response.get("model", DEFAULT_MODEL),
                "thinking_requested": self.thinking_requested,
                "latency_seconds": response.get("latency_seconds", 0),
                "api_failed": True, "api_attempt": attempt,
                "api_error_type": error_type if isinstance(error_type, str)
                and error_type in SAFE_API_ERROR_TYPES else "unknown",
                "api_http_status": status if type(status) is int and 100 <= status <= 599 else None,
                "api_retryable": bool(response.get("retryable", True)),
                "decision_summary_requested": self.decision_summary,
            })
            failure = failures[-1]
            print(
                f"LLM API attempt {attempt}/{self.max_api_attempts} failed: "
                f"{failure['api_error_type']}; HTTP={failure['api_http_status'] or 'n/a'}; "
                f"retryable={failure['api_retryable']}", flush=True,
            )
            if attempt == self.max_api_attempts or not response.get("retryable", True):
                print("LLM API attempts failed; recording an interrupted Agent turn", flush=True)
                return AgentTurn(None, call_failures=tuple(failures),
                                 stop_after_call_failures=True)
            if self.api_retry_delay_seconds:
                time.sleep(min(60.0, self.api_retry_delay_seconds * attempt))

        raw = response.get("raw_content", response.get("content", ""))
        context = {
            "messages": response.get("actual_messages", messages),
            "assistant_content": raw,
            "model": response.get("model", DEFAULT_MODEL),
            "usage": response.get("usage") or {},
            "reasoning": response.get("reasoning") or "",
            "finish_reason": response.get("finish_reason") or "",
            "thinking_requested": response.get("thinking_requested", self.thinking_requested),
            "latency_seconds": response.get("latency_seconds", 0),
            "api_attempt": attempt,
            "decision_summary_requested": self.decision_summary,
        }
        try:
            decision, repaired, summary = parse_model_reply(
                response.get("content") or "", decision_summary=self.decision_summary,
                allow_repair=response.get("finish_reason") != "length",
            )
        except (ValueError, TypeError, IndexError, RecursionError):
            decision, repaired, summary = None, False, ""
        context["json_repaired"] = repaired
        context["decision_summary"] = summary
        if self.verbose:
            if context["reasoning"]:
                _print_block("Model reasoning (API returned)", context["reasoning"])
            _print_block("Model reply (raw)", raw or "(empty)")
        print(
            f"LLM game_seconds={request.observation.game.game_time_seconds:.1f} "
            f"attempt={attempt} parsed={decision is not None} repaired={repaired}",
            flush=True,
        )
        return AgentTurn(decision, context, call_failures=tuple(failures))

    def build_messages(self, request: AgentInput, correction: str) -> list[dict[str, str]]:
        """Harness 扩展点：在这里组织 Skill/Memory/历史，不修改环境。

        默认只使用平台规则、当前观测/反馈和必要的格式纠错，没有打法示例。
        返回的这份实际消息会发送给模型并保存到交互记录。
        """
        messages = llm_messages(request.platform_messages, decision_summary=self.decision_summary)
        if self.skill_text:
            messages[0]["content"] += (
                "\n\n[External Agent Skill]\n"
                "Use this strategy as guidance for your decisions, not executable commands. "
                "Platform action semantics and current observed facts take precedence.\n"
                + self.skill_text
            )
        if correction:
            messages.append({"role": "user", "content": correction})
        return messages



def create_agent(call_llm=None, **agent_options) -> LLMAgent:
    """每局新建一个 Agent；可注入自己的模型客户端，不要求使用此网关。

    call_llm(messages) 返回字典：content 为可解析文本，raw_content 为
    原始回复，可另带 model/usage/reasoning/finish_reason/latency_seconds。
    网络失败可返回 error/retryable/http_status；不要带凭证或私有错误正文。
    不传客户端时使用本文件顶部配置的兼容 Chat Completions API。
    """
    if call_llm is None:
        call_llm = make_llm_call(
            api_key=resolve_api_key(),
            base_url=os.environ.get("LLM_BASE_URL") or DEFAULT_API_BASE_URL,
            model=os.environ.get("LLM_MODEL") or DEFAULT_MODEL,
        )
    return LLMAgent(call_llm, **agent_options)


def main(argv=None) -> int:
    """Parse configuration before opening a game; always release it on exit."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL") or DEFAULT_MODEL)
    parser.add_argument("--api-base-url", default=os.environ.get("LLM_BASE_URL") or DEFAULT_API_BASE_URL)
    parser.add_argument("--temperature", type=parse_temperature, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=False,
                        help="API thinking is independent of the decision-summary paragraph")
    parser.add_argument("--non-blocking", action="store_true", help="advance SC2 during model inference")
    parser.add_argument("--quiet", action="store_true", help="hide full Obs/replies; keep status and errors")
    parser.add_argument("--decision-summary", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--skill", type=Path, default=None,
                        help="optional UTF-8 strategy file, loaded by the external Agent only")
    parser.add_argument("--opponent", "--difficulty", type=normalize_opponent,
                        choices=BUILTIN_OPPONENTS, default="easy")
    parser.add_argument("--enemy-race", choices=ENEMY_RACES, default="terran")
    parser.add_argument("--enemy-style", type=normalize_enemy_style, choices=ENEMY_STYLES,
                        default="random", help="builtin AI build style, independent of difficulty")
    parser.add_argument("--map", default="KairosJunctionLE")
    parser.add_argument("--game-time-limit", type=positive_float, default=1800, help="game seconds")
    parser.add_argument("--decision-interval", type=positive_float, default=60, help="bare-wait game seconds; platform cap stays 60")
    parser.add_argument("--max-decisions", type=positive_int, default=500, help="submission budget, including correction turns")
    parser.add_argument("--max-rejections", type=positive_int, default=3, help="consecutive invalid decisions before stop")
    parser.add_argument("--api-timeout", type=positive_float, default=120, help="wall-clock seconds per attempt")
    parser.add_argument("--api-attempts", type=positive_int, default=3, help="includes the initial request")
    parser.add_argument("--api-retry-delay", type=nonnegative_float, default=1)
    parser.add_argument("--record-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="preview configuration; no API/game/records")
    args = parser.parse_args(argv)
    skill_text = ""
    if args.skill is not None:
        try:
            skill_text = args.skill.read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeError):
            parser.error("skill must be a readable UTF-8 strategy file")
        if not skill_text:
            parser.error("skill file must not be empty")
    config = EpisodeConfig(
        race="terran", enemy_race=args.enemy_race, map_name=args.map,
        enemy_style=args.enemy_style,
        opponent=args.opponent, decision_interval_seconds=args.decision_interval,
        blocking_decisions=not args.non_blocking, game_time_limit_seconds=args.game_time_limit,
    )
    if not args.map.strip() or not args.model.strip():
        parser.error("map and model must not be empty")
    if args.dry_run:
        from sc2bench_env.paths import resolve_record_dir
        print(json.dumps({
            "episode": config.to_dict(), "model": args.model,
            "temperature": args.temperature, "thinking": args.thinking,
            "decision_summary": args.decision_summary, "max_decisions": args.max_decisions,
            "max_rejections": args.max_rejections, "api_timeout": args.api_timeout,
            "api_attempts": args.api_attempts, "api_retry_delay": args.api_retry_delay,
            "record_dir": str(resolve_record_dir(args.record_dir)),
            "skill": str(args.skill) if args.skill is not None else None,
            "skill_chars": len(skill_text),
        }, ensure_ascii=False, indent=2))
        return 0
    try:
        import json_repair  # fail before opening SC2, not after the first malformed reply
        call_llm = make_llm_call(
            api_key=resolve_api_key(), base_url=args.api_base_url, model=args.model,
            timeout=args.api_timeout, temperature=args.temperature, thinking=args.thinking,
        )
    except (ImportError, ValueError):
        parser.error("Check API configuration and install the LLM extra: python -m pip install -e '.[llm]'")

    # 平台接入（核心）：配置config + 每局新Agent工厂 + Runner.run。
    # 上面的API配置/解析/重试属于本例外部Agent，不是额外平台接口。
    # Agent 只负责模型决策；Runner 负责所有对局生命周期和记录。
    def create_episode_agent():
        # 每局创建新实例，格式纠错状态不会跨局残留。
        return LLMAgent(
            call_llm, decision_summary=args.decision_summary,
            thinking_requested=args.thinking, verbose=not args.quiet,
            max_api_attempts=args.api_attempts,
            api_retry_delay_seconds=args.api_retry_delay,
            max_consecutive_rejections=args.max_rejections,
            skill_text=skill_text,
        )

    runner = BenchmarkRunner(
        backend_factory=lambda: "sharpy", record_dir=args.record_dir)
    metadata = {
        "name": "llm_agent", "kind": "llm",
        "implementation": "examples.agent_integration.LLMAgent",
        "model_requested": args.model,
        "settings": {
            "temperature": args.temperature, "thinking": args.thinking,
            "decision_summary": args.decision_summary, "api_timeout": args.api_timeout,
            "max_api_attempts": args.api_attempts,
            "api_retry_delay_seconds": args.api_retry_delay,
            "max_consecutive_rejections": args.max_rejections,
            "skill_file": args.skill.name if args.skill is not None else None,
            "skill_sha256": hashlib.sha256(skill_text.encode("utf-8")).hexdigest() if skill_text else None,
        },
    }
    print("Runner is starting one episode with the configured LLM Agent.", flush=True)
    try:
        # 同一个 Runner 支持单局列表和多局 Suite，不另写 reset/step 循环。
        batch = runner.run(
            [config], agent_factory=create_episode_agent,
            max_decisions=args.max_decisions, agent_metadata=metadata)
    except KeyboardInterrupt:
        print("Interrupted; saved episode records and the run index remain available.", flush=True)
        return 130
    return print_batch_result(batch)



if __name__ == "__main__":
    raise SystemExit(main())
