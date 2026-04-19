"""
llm_utils.py — LLM factory, JSON validation + repair, safe invoke.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx
from pydantic import Field
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from .config import Config
from .runtime_settings import (
    get_custom_runtime,
    get_ollama_runtime,
    get_provider_secret,
    load_runtime_settings,
)

# ─────────────────────────────────────────────────────────────
# LLM Factory
# ─────────────────────────────────────────────────────────────

_llm_cache: dict = {}


def _normalize_openai_chat_url(url: str) -> str:
    cleaned = (url or "").strip().rstrip("/")
    if cleaned.endswith("/chat/completions"):
        return cleaned
    if cleaned.endswith("/v1"):
        return cleaned + "/chat/completions"
    return cleaned + "/v1/chat/completions"


def _serialize_messages(messages: list[BaseMessage]) -> list[dict[str, str]]:
    payload = []
    for message in messages:
        role = "user"
        if isinstance(message, SystemMessage):
            role = "system"
        elif isinstance(message, AIMessage):
            role = "assistant"
        payload.append({"role": role, "content": str(message.content)})
    return payload


class OpenAICompatibleChatModel(BaseChatModel):
    endpoint_url: str
    model_name: str
    api_key: str = ""
    auth_header: str = "Authorization"
    temperature: float = 0.3
    timeout: float = 120.0
    max_tokens: int = 4096
    extra_headers: dict[str, str] = Field(default_factory=dict)

    @property
    def _llm_type(self) -> str:
        return "openai_compatible"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {
            "endpoint_url": self.endpoint_url,
            "model_name": self.model_name,
        }

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager=None, **kwargs) -> ChatResult:
        headers = dict(self.extra_headers)
        if self.api_key:
            if self.auth_header.lower() == "authorization":
                headers[self.auth_header] = f"Bearer {self.api_key}"
            else:
                headers[self.auth_header] = self.api_key

        payload = {
            "model": self.model_name,
            "messages": _serialize_messages(messages),
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "stream": False,
        }
        if stop:
            payload["stop"] = stop

        response = httpx.post(
            _normalize_openai_chat_url(self.endpoint_url),
            json=payload,
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception as e:
            raise RuntimeError(f"Unsupported response payload from custom endpoint: {data}") from e
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])


def get_llm(provider: str = "auto") -> tuple:
    """Return (llm, provider_name). Caches per provider. Auto falls back."""
    runtime = load_runtime_settings()
    gemini_key = get_provider_secret("gemini", runtime)
    groq_key = get_provider_secret("groq", runtime)
    groq_key_2 = get_provider_secret("groq_2", runtime)
    ollama_model, ollama_base_url = get_ollama_runtime(runtime)
    custom_runtime = get_custom_runtime(runtime)
    preferred_provider = (
        runtime.get("provider_registry", {}).get("defaultProvider")
        or runtime.get("workflow_defaults", {}).get("defaultProvider")
        or provider
    )

    cache_key = json.dumps({
        "provider": provider,
        "preferred_provider": preferred_provider,
        "gemini_model": Config.GEMINI_MODEL,
        "gemini_key": gemini_key,
        "groq_model": Config.GROQ_MODEL,
        "groq_model_2": Config.GROQ_MODEL_2,
        "groq_model_3": Config.GROQ_MODEL_3,
        "groq_key": groq_key,
        "groq_key_2": groq_key_2,
        "ollama_model": ollama_model,
        "ollama_base_url": ollama_base_url,
        "custom_runtime": custom_runtime,
    }, sort_keys=True)
    if cache_key in _llm_cache:
        return _llm_cache[cache_key]

    def _try_gemini():
        from langchain_google_genai import ChatGoogleGenerativeAI
        if not gemini_key:
            raise RuntimeError("Gemini API key is not configured in runtime settings or environment.")
        return ChatGoogleGenerativeAI(
            model=Config.GEMINI_MODEL,
            google_api_key=gemini_key,
            temperature=0.3,
            max_retries=1,
            timeout=60,
        )

    def _try_groq():
        from langchain_groq import ChatGroq
        if not groq_key:
            raise RuntimeError("Groq API key is not configured in runtime settings or environment.")
        return ChatGroq(
            model=Config.GROQ_MODEL,
            api_key=groq_key,
            temperature=0.3,
            max_retries=1,
            timeout=60,
        )

    def _try_groq_2():
        from langchain_groq import ChatGroq
        if not groq_key_2:
            raise RuntimeError("Groq fallback API key is not configured in runtime settings or environment.")
        return ChatGroq(
            model=Config.GROQ_MODEL_2,
            api_key=groq_key_2,
            temperature=0.3,
            max_retries=1,
            timeout=60,
        )

    def _try_groq_3():
        from langchain_groq import ChatGroq
        if not groq_key_2:
            raise RuntimeError("Groq fallback API key is not configured in runtime settings or environment.")
        return ChatGroq(
            model=Config.GROQ_MODEL_3,
            api_key=groq_key_2,
            temperature=0.3,
            max_retries=1,
            timeout=60,
        )

    def _try_ollama():
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=ollama_model,
            base_url=ollama_base_url,
            temperature=0.3,
            timeout=120,
        )

    def _try_custom():
        if not custom_runtime.get("endpoint_url"):
            raise RuntimeError("No custom endpoint URL configured.")
        return OpenAICompatibleChatModel(
            endpoint_url=custom_runtime["endpoint_url"],
            model_name=custom_runtime["model"] or "local-model",
            api_key=custom_runtime.get("api_key", ""),
            auth_header=custom_runtime.get("auth_header", "Authorization"),
            temperature=0.3,
            timeout=120,
        )

    def _append_candidate(container, name, factory):
        if any(existing_name == name for existing_name, _ in container):
            return
        container.append((name, factory))

    if provider == "gemini":
        candidates = [("gemini", _try_gemini)]
    elif provider == "groq":
        candidates = [("groq", _try_groq)]
        if groq_key_2:
            candidates.append(("groq_2", _try_groq_2))
            candidates.append(("groq_3", _try_groq_3))
    elif provider == "groq_2":
        candidates = [("groq_2", _try_groq_2)]
    elif provider == "ollama":
        candidates = [("ollama", _try_ollama)]
    elif provider == "custom":
        candidates = [("custom", _try_custom)]
    else:  # auto
        candidates = []
        preferred_map = {
            "custom": ("custom", _try_custom),
            "gemini": ("gemini", _try_gemini),
            "groq": ("groq", _try_groq),
            "groq_2": ("groq_2", _try_groq_2),
            "ollama": ("ollama", _try_ollama),
        }
        if preferred_provider in preferred_map:
            _append_candidate(candidates, *preferred_map[preferred_provider])
        if custom_runtime.get("endpoint_url"):
            _append_candidate(candidates, "custom", _try_custom)
        if gemini_key:
            _append_candidate(candidates, "gemini", _try_gemini)
        if groq_key:
            _append_candidate(candidates, "groq", _try_groq)
        if groq_key_2:
            _append_candidate(candidates, "groq_2", _try_groq_2)
            _append_candidate(candidates, "groq_3", _try_groq_3)
        _append_candidate(candidates, "ollama", _try_ollama)

    instantiated = []
    for name, factory in candidates:
        try:
            instantiated.append((factory(), name))
        except Exception as e:
            print(f"[LLM] {name} instantiation failed: {e}")
    
    if not instantiated:
        raise RuntimeError(f"All LLM providers for {provider} failed to instantiate.")
        
    primary_llm, primary_name = instantiated[0]
    fallbacks = [llm for llm, name in instantiated[1:]]
    
    if fallbacks:
        primary_llm = primary_llm.with_fallbacks(fallbacks)
        primary_name = f"{primary_name} (with {len(fallbacks)} fallbacks)"
        
    _llm_cache[cache_key] = (primary_llm, primary_name)
    print(f"[LLM] Provider {provider}: {primary_name}")
    return primary_llm, primary_name


# ─────────────────────────────────────────────────────────────
# JSON Validation + Repair
# ─────────────────────────────────────────────────────────────

def _extract_json_block(text: str) -> str | None:
    """Extract JSON from ```json ... ``` code blocks."""
    patterns = [
        r"```json\s*\n(.*?)```",
        r"```\s*\n(\{.*?\})```",
    ]
    for pat in patterns:
        match = re.search(pat, text, re.DOTALL)
        if match:
            return match.group(1).strip()
    return None


def _fix_common_json_issues(text: str) -> str:
    """Fix trailing commas, single quotes, and other common issues."""
    # Remove trailing commas before } or ]
    text = re.sub(r",\s*([}\]])", r"\1", text)
    # Try to fix single-quoted strings (crude but covers common cases)
    # Only if the text has no double quotes at all
    if '"' not in text and "'" in text:
        text = text.replace("'", '"')
    return text


def validate_llm_json(
    raw_text: str,
    required_keys: list[str],
    llm: Any = None,
    schema_description: str = "",
    max_repair_attempts: int = 2,
) -> tuple[dict | None, str]:
    """
    Validate and repair LLM JSON output.

    Returns (parsed_dict, error_message).
    If successful, error_message is empty.
    If all repair fails, returns (None, error_description).
    """
    attempts = []

    # Step 1: Direct parse
    text = raw_text.strip()
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            missing = [k for k in required_keys if k not in result]
            if not missing:
                return result, ""
            attempts.append(f"Parsed but missing keys: {missing}")
    except json.JSONDecodeError as e:
        attempts.append(f"Direct parse failed: {e}")

    # Step 2: Extract from code block
    block = _extract_json_block(text)
    if block:
        try:
            result = json.loads(block)
            if isinstance(result, dict):
                missing = [k for k in required_keys if k not in result]
                if not missing:
                    return result, ""
                attempts.append(f"Code block parsed but missing keys: {missing}")
        except json.JSONDecodeError as e:
            attempts.append(f"Code block parse failed: {e}")
            # Try fixing common issues in code block
            fixed = _fix_common_json_issues(block)
            try:
                result = json.loads(fixed)
                if isinstance(result, dict):
                    missing = [k for k in required_keys if k not in result]
                    if not missing:
                        return result, ""
            except json.JSONDecodeError:
                pass

    # Step 3: Fix common issues on raw text
    # Try to find JSON object in the text
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        json_candidate = text[brace_start:brace_end + 1]
        fixed = _fix_common_json_issues(json_candidate)
        try:
            result = json.loads(fixed)
            if isinstance(result, dict):
                missing = [k for k in required_keys if k not in result]
                if not missing:
                    return result, ""
                attempts.append(f"Extracted JSON but missing keys: {missing}")
        except json.JSONDecodeError as e:
            attempts.append(f"Extracted JSON parse failed: {e}")

    # Step 4: LLM repair (if LLM provided)
    if llm is not None:
        for attempt_idx in range(max_repair_attempts):
            repair_prompt = (
                "Your previous output was not valid JSON. "
                f"Errors: {'; '.join(attempts[-2:])}\n\n"
                f"Required keys: {required_keys}\n"
            )
            if schema_description:
                repair_prompt += f"Schema: {schema_description}\n"
            repair_prompt += (
                "\nPlease return ONLY valid JSON (no markdown, no explanation). "
                "Start with { and end with }."
            )
            try:
                resp = llm.invoke([
                    SystemMessage(content="You are a JSON repair assistant. Return ONLY valid JSON."),
                    HumanMessage(content=repair_prompt + f"\n\nOriginal text to repair:\n{raw_text[:2000]}"),
                ])
                repair_text = resp.content.strip()

                # Try parsing repaired output
                parsed = None
                try:
                    parsed = json.loads(repair_text)
                except json.JSONDecodeError:
                    block = _extract_json_block(repair_text)
                    if block:
                        try:
                            parsed = json.loads(block)
                        except json.JSONDecodeError:
                            pass
                    if parsed is None:
                        bs = repair_text.find("{")
                        be = repair_text.rfind("}")
                        if bs != -1 and be > bs:
                            try:
                                parsed = json.loads(
                                    _fix_common_json_issues(repair_text[bs:be+1])
                                )
                            except json.JSONDecodeError:
                                pass

                if isinstance(parsed, dict):
                    missing = [k for k in required_keys if k not in parsed]
                    if not missing:
                        return parsed, ""
                    attempts.append(f"Repair #{attempt_idx+1} missing keys: {missing}")
                else:
                    attempts.append(f"Repair #{attempt_idx+1} did not produce a dict")

            except Exception as e:
                attempts.append(f"Repair #{attempt_idx+1} LLM call failed: {e}")

    return None, f"JSON validation failed after all attempts: {'; '.join(attempts)}"


# ─────────────────────────────────────────────────────────────
# Safe LLM Invoke
# ─────────────────────────────────────────────────────────────

def invoke_llm_structured(llm, messages: list, pydantic_schema: type) -> tuple[Any | None, str]:
    """Invoke an LLM and force output to conform to the given Pydantic schema."""
    llms_to_try = []
    if hasattr(llm, "runnable") and hasattr(llm, "fallbacks"):
        llms_to_try = [llm.runnable] + list(llm.fallbacks)
    else:
        llms_to_try = [llm]

    last_err = None
    for try_llm in llms_to_try:
        try:
            if hasattr(try_llm, "with_structured_output"):
                structured_llm = try_llm.with_structured_output(pydantic_schema)
                resp = structured_llm.invoke(messages)
                return resp, ""
        except Exception as e:
            print(f"[LLM] Native structured output failed for {try_llm.__class__.__name__}: {e}. Trying next...")
            last_err = e
            continue

    # Fallback to schema description + parse + Pydantic validation
    schema_desc = json.dumps(pydantic_schema.model_json_schema(), indent=2)
    messages = list(messages)
    messages[0] = SystemMessage(
        content=messages[0].content + f"\n\nYou MUST return raw JSON conforming strictly to this schema:\n{schema_desc}"
    )

    try:
        resp = llm.invoke(messages)
        text = resp.content.strip()
        parsed, parse_err = validate_llm_json(
            text, required_keys=list(pydantic_schema.model_fields.keys()),
            llm=llm, schema_description=schema_desc
        )
        if parsed:
            return pydantic_schema(**parsed), ""
        return None, parse_err
    except Exception as e:
        return None, str(e)


def invoke_llm(llm, messages: list, expect_json: bool = False) -> str:
    """Invoke an LLM with messages. Returns content string."""
    try:
        resp = llm.invoke(messages)
        return resp.content.strip()
    except Exception as e:
        print(f"[LLM] Invoke failed: {e}")
        raise
