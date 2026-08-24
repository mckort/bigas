from __future__ import annotations

import json
import logging
import warnings
from typing import Any, Dict, List, Optional, Tuple

# Suppress FutureWarnings from Google libs (e.g. Python 3.10 EOL) when using Gemini
warnings.filterwarnings("ignore", category=FutureWarning, module="google.api_core")
try:
    warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")
    import google.generativeai as genai  # type: ignore
    try:
        from google.generativeai.types import HarmCategory, HarmBlockThreshold  # type: ignore
        _SAFETY_BLOCK_NONE = [
            {"category": HarmCategory.HARM_CATEGORY_HARASSMENT, "threshold": HarmBlockThreshold.BLOCK_NONE},
            {"category": HarmCategory.HARM_CATEGORY_HATE_SPEECH, "threshold": HarmBlockThreshold.BLOCK_NONE},
            {"category": HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, "threshold": HarmBlockThreshold.BLOCK_NONE},
            {"category": HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, "threshold": HarmBlockThreshold.BLOCK_NONE},
        ]
    except (ImportError, AttributeError):
        _SAFETY_BLOCK_NONE = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
except ImportError:  # pragma: no cover - optional dependency
    genai = None  # type: ignore
    _SAFETY_BLOCK_NONE = []

from bigas.llm.client import LLMClient
from bigas.llm.completion import LLMCompletion, ToolCall
from bigas.llm.usage import TokenUsage, usage_from_mapping

logger = logging.getLogger(__name__)


def _usage_from_gemini_response(response: Any) -> TokenUsage:
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return TokenUsage()
    if isinstance(meta, dict):
        return usage_from_mapping(meta)
    # Collect both snake_case and camelCase attributes — usage_from_mapping handles key resolution.
    raw = {
        "prompt_token_count": getattr(meta, "prompt_token_count", None),
        "candidates_token_count": getattr(meta, "candidates_token_count", None),
        "thoughts_token_count": getattr(meta, "thoughts_token_count", None),
        "total_token_count": getattr(meta, "total_token_count", None),
        "promptTokenCount": getattr(meta, "promptTokenCount", None),
        "candidatesTokenCount": getattr(meta, "candidatesTokenCount", None),
        "thoughtsTokenCount": getattr(meta, "thoughtsTokenCount", None),
        "totalTokenCount": getattr(meta, "totalTokenCount", None),
    }
    return usage_from_mapping(raw)


def _as_plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _as_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_plain(v) for v in value]
    try:
        return {str(k): _as_plain(v) for k, v in dict(value).items()}
    except Exception:
        return str(value)


def _openai_tools_to_gemini_decls(tools: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    decls: List[Dict[str, Any]] = []
    for tool in tools or []:
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = str((fn or {}).get("name") or "").strip()
        if not name:
            continue
        params = (fn or {}).get("parameters") or {"type": "object", "properties": {}}
        if not isinstance(params, dict):
            params = {"type": "object", "properties": {}}
        decls.append(
            {
                "name": name,
                "description": str((fn or {}).get("description") or "")[:1024],
                "parameters": params,
            }
        )
    return decls


def gemini_contents_from_messages(
    messages: List[Dict[str, Any]],
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """Split system text and convert chat+tool turns into Gemini contents."""
    system_parts: List[str] = []
    contents: List[Dict[str, Any]] = []
    for message in messages:
        role = (message.get("role") or "user").lower()
        content = message.get("content")
        text = content.strip() if isinstance(content, str) else (str(content).strip() if content else "")
        if role == "system":
            if text:
                system_parts.append(text)
            continue
        if role == "assistant":
            parts: List[Dict[str, Any]] = []
            if text:
                parts.append({"text": text})
            for raw_call in message.get("tool_calls") or []:
                fn = raw_call.get("function") if isinstance(raw_call.get("function"), dict) else raw_call
                name = str((fn or {}).get("name") or raw_call.get("name") or "").strip()
                args = (fn or {}).get("arguments") if isinstance(fn, dict) else raw_call.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args or "{}")
                    except json.JSONDecodeError:
                        args = {}
                if not isinstance(args, dict):
                    args = {}
                if name:
                    parts.append({"function_call": {"name": name, "args": args}})
            if parts:
                contents.append({"role": "model", "parts": parts})
            continue
        if role == "tool":
            name = str(message.get("name") or "tool").strip() or "tool"
            function_response = {
                "function_response": {
                    "name": name,
                    "response": {"result": text},
                }
            }
            if (
                contents
                and contents[-1].get("role") == "user"
                and any(
                    isinstance(part, dict) and "function_response" in part
                    for part in contents[-1].get("parts") or []
                )
            ):
                contents[-1]["parts"].append(function_response)
            else:
                contents.append({"role": "user", "parts": [function_response]})
            continue
        if text:
            contents.append({"role": "user", "parts": [{"text": text}]})
    system_instruction = "\n\n".join(system_parts) if system_parts else None
    return system_instruction, contents


def _send_payload(content: Dict[str, Any]) -> Any:
    parts = content.get("parts") or []
    if (
        len(parts) == 1
        and isinstance(parts[0], dict)
        and set(parts[0].keys()) == {"text"}
        and isinstance(parts[0].get("text"), str)
    ):
        return parts[0]["text"]
    return parts


def _part_function_call(part: Any) -> Optional[Any]:
    if isinstance(part, dict):
        return part.get("function_call")
    return getattr(part, "function_call", None)


def _part_text(part: Any) -> Optional[str]:
    if isinstance(part, dict):
        text = part.get("text")
    else:
        text = getattr(part, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    return None


def tool_calls_from_gemini_parts(parts: Any) -> Tuple[str, Tuple[ToolCall, ...]]:
    text_parts: List[str] = []
    calls: List[ToolCall] = []
    for index, part in enumerate(parts or []):
        text = _part_text(part)
        if text:
            text_parts.append(text)
        raw = _part_function_call(part)
        if not raw:
            continue
        name = str(
            raw.get("name") if isinstance(raw, dict) else getattr(raw, "name", "") or ""
        ).strip()
        if not name:
            continue
        args = raw.get("args") if isinstance(raw, dict) else getattr(raw, "args", None)
        plain = _as_plain(args)
        if not isinstance(plain, dict):
            plain = {}
        calls.append(ToolCall(id=f"call_{index}_{name}", name=name, arguments=plain))
    return "\n".join(text_parts), tuple(calls)


def _finish_reason_str(finish_reason: Any) -> Optional[str]:
    if finish_reason is None:
        return None
    name = getattr(finish_reason, "name", None)
    if isinstance(name, str) and name:
        return name
    return str(finish_reason)


class GeminiLLMClient(LLMClient):
    """
    LLMClient implementation backed by Google's Gemini API.

    This uses the `google-generativeai` package and assumes an API key
    is provided explicitly (no implicit ADC in this helper).
    """

    def __init__(self, *, api_key: str, model: str) -> None:
        if genai is None:
            raise RuntimeError(
                "google-generativeai is not installed. "
                "Add it to requirements to use Gemini."
            )
        genai.configure(api_key=api_key)
        self._model_name = model
        self._model = genai.GenerativeModel(model)

    @property
    def model_name(self) -> str:
        return self._model_name

    def complete(
        self,
        messages: List[Dict[str, str]],
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> str:
        return self.complete_detailed(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        ).text

    def complete_detailed(
        self,
        messages: List[Dict[str, str]],
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> LLMCompletion:
        tools = kwargs.pop("tools", None)
        kwargs.pop("tool_choice", None)
        function_decls = _openai_tools_to_gemini_decls(tools if isinstance(tools, list) else None)
        gemini_tools = [{"function_declarations": function_decls}] if function_decls else None

        system_instruction, rest = gemini_contents_from_messages(messages)
        if not rest:
            return LLMCompletion(text="", finish_reason=None)

        generation_config: Dict[str, Any] = {}
        if max_tokens is not None:
            generation_config["max_output_tokens"] = max_tokens
        if temperature is not None:
            generation_config["temperature"] = temperature
        # Cap thinking so long reviews keep budget for visible text. Gemini thinking
        # models share max_output_tokens between thinking + answer.
        thinking_budget = kwargs.pop("thinking_budget", None)
        if thinking_budget is not None:
            try:
                generation_config["thinking_config"] = {
                    "thinking_budget": int(thinking_budget),
                }
            except (TypeError, ValueError):
                logger.warning("Ignoring invalid thinking_budget=%r", thinking_budget)
        extra_cfg = kwargs.pop("generation_config", None)
        if isinstance(extra_cfg, dict):
            generation_config.update(extra_cfg)
        gen_cfg = generation_config if generation_config else None

        model_kwargs: Dict[str, Any] = {}
        if system_instruction:
            model_kwargs["system_instruction"] = system_instruction
        if gemini_tools:
            model_kwargs["tools"] = gemini_tools
        if model_kwargs:
            model = genai.GenerativeModel(self._model_name, **model_kwargs)
        else:
            model = self._model

        def _call(active_model: Any, active_cfg: Any) -> Any:
            if len(rest) == 1 and rest[0]["role"] == "user":
                return active_model.generate_content(
                    _send_payload(rest[0]),
                    generation_config=active_cfg,
                    safety_settings=_SAFETY_BLOCK_NONE if _SAFETY_BLOCK_NONE else None,
                    **kwargs,
                )
            history = rest[:-1]
            chat = active_model.start_chat(history=history)
            return chat.send_message(
                _send_payload(rest[-1]),
                generation_config=active_cfg,
                safety_settings=_SAFETY_BLOCK_NONE if _SAFETY_BLOCK_NONE else None,
                **kwargs,
            )

        try:
            response = _call(model, gen_cfg)
        except Exception:
            # Retry once without thinking_config if the SDK/model rejects it.
            if "thinking_config" in (generation_config or {}):
                logger.warning(
                    "Gemini call failed with thinking_config; retrying without it.",
                    exc_info=True,
                )
                generation_config.pop("thinking_config", None)
                gen_cfg = generation_config if generation_config else None
                try:
                    response = _call(model, gen_cfg)
                except Exception:
                    logger.error("Gemini API call failed in complete_detailed()", exc_info=True)
                    raise
            else:
                logger.error("Gemini API call failed in complete_detailed()", exc_info=True)
                raise

        usage = _usage_from_gemini_response(response)
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return LLMCompletion(text="", finish_reason=None, usage=usage)
        candidate = candidates[0]
        finish_reason = _finish_reason_str(getattr(candidate, "finish_reason", None))

        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) if content else None or []

        text, tool_calls = tool_calls_from_gemini_parts(parts)
        if text or tool_calls:
            return LLMCompletion(
                text=text,
                finish_reason=finish_reason,
                usage=usage,
                tool_calls=tool_calls,
            )

        # Fallback: do NOT use response.text / candidate.text — those SDK "quick
        # accessors" raise ValueError when no valid Part exists (e.g. MAX_TOKENS
        # with empty parts). Use to_dict() and collect nested "text" strings only.
        try:
            to_dict = getattr(response, "to_dict", None)
            if callable(to_dict):
                raw = to_dict() or {}

                found: List[str] = []
                stack: List[Any] = [raw]
                while stack and len(found) < 5:
                    cur = stack.pop()
                    if isinstance(cur, dict):
                        for k, v in cur.items():
                            if k == "text" and isinstance(v, str) and v.strip():
                                found.append(v.strip())
                            elif isinstance(v, (dict, list)):
                                stack.append(v)
                    elif isinstance(cur, list):
                        stack.extend(cur)

                if found:
                    logger.warning(
                        "Gemini fallback extracted %d text string(s) from response.to_dict().",
                        len(found),
                    )
                    return LLMCompletion(
                        text="\n".join(found),
                        finish_reason=finish_reason,
                        usage=usage,
                    )
        except Exception:
            pass

        safety_ratings = getattr(candidate, "safety_ratings", None)
        logger.warning(
            "Gemini response had no text parts (finish_reason=%s, safety_ratings=%s).",
            finish_reason,
            safety_ratings,
        )
        return LLMCompletion(text="", finish_reason=finish_reason, usage=usage)
