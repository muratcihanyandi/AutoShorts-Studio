import json
import logging
import re
from time import perf_counter
from typing import List, Optional, Tuple, Dict, Any

from loguru import logger
from openai import AzureOpenAI, OpenAI
from openai.types.chat import ChatCompletion

from app.config import config
from app.models.exception import LLMGenerationError
from app.models.llm_provider import DEFAULT_LLM_PROVIDER_ID, get_llm_provider

_max_retries = 5
MIN_SCRIPT_PARAGRAPH_NUMBER = 1
MAX_SCRIPT_PARAGRAPH_NUMBER = 10
MAX_SCRIPT_PROMPT_LENGTH = 2000
MAX_SCRIPT_SYSTEM_PROMPT_LENGTH = 8000
_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
_UNCLOSED_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*$", re.IGNORECASE | re.DOTALL)
_URL_USERINFO_RE = re.compile(
    r"((?:https?|wss?)://)([^/\s?#@]*:[^/\s?#@]*@)", re.IGNORECASE
)
_SENSITIVE_QUERY_RE = re.compile(
    r"([?&](?:api[_-]?key|access[_-]?token|token|key|secret|password)=)([^&#\s]+)",
    re.IGNORECASE,
)

DEFAULT_SCRIPT_SYSTEM_PROMPT = """
# Role: Video Script Generator

## Goals:
Generate a script for a video, depending on the subject of the video.

## Constrains:
1. the script is to be returned as a string with the specified number of paragraphs.
2. do not under any circumstance reference this prompt in your response.
3. get straight to the point, don't start with unnecessary things like, "welcome to this video".
4. you must not include any type of markdown or formatting in the script, never use a title.
5. only return the raw content of the script.
6. do not include "voiceover", "narrator" or similar indicators of what should be spoken at the beginning of each paragraph or line.
7. you must not mention the prompt, or anything about the script itself. also, never talk about the amount of paragraphs or lines. just write the script.
8. respond in the same language as the video subject.
9. for short vertical videos (Shorts/Reels/TikTok), the first sentence MUST be a powerful retention hook (pattern interrupt, shocking fact, or curiosity gap) within the first 3 seconds to keep viewers from swiping away. Never use greetings.
""".strip()


def _normalize_text_response(content, llm_provider: str) -> str:
    # 不同 LLM SDK 在异常或被拦截场景下，可能返回 None、空字符串，
    # 甚至返回非字符串对象。这里统一做兜底校验，避免后续直接调用
    # `.replace()` 时抛出 `NoneType` 之类的属性错误。
    if content is None:
        raise ValueError(f"[{llm_provider}] returned empty text content")

    if not isinstance(content, str):
        raise TypeError(
            f"[{llm_provider}] returned non-text content: {type(content).__name__}"
        )

    # MiniMax M3、DeepSeek R1 这类 reasoning 模型可能会把内部推理包在
    # `<think>...</think>` 中返回。视频脚本和关键词只需要最终可朗读文本，
    # 如果不在服务层统一清理，WebUI、字幕和配音都会把思考过程当正文处理。
    content = _THINK_BLOCK_RE.sub("", content)
    content = _UNCLOSED_THINK_BLOCK_RE.sub("", content).strip()
    if not content:
        raise ValueError(f"[{llm_provider}] returned empty text content")

    return content.replace("\n", "")


def _sanitize_error_message(error: object) -> str:
    """
    清理返回给 WebUI/API 的错误信息，避免自定义 base_url 中的凭据泄露。

    一些 OpenAI-compatible SDK 会把请求 URL 原样拼进异常信息。如果用户为了
    代理网关配置了 `https://user:pass@example.com/v1`，直接返回 `str(e)`
    就会把密码暴露给页面、API 调用方或后续日志。这里仅处理错误文案，不改变
    实际请求地址，避免影响正常调用链路。
    """
    message = str(error)
    message = _URL_USERINFO_RE.sub(r"\1***:***@", message)
    message = _SENSITIVE_QUERY_RE.sub(r"\1***", message)
    return message


def _extract_chat_completion_text(response, llm_provider: str) -> str:
    # OpenAI 兼容接口在异常场景下，可能返回没有 choices、
    # 或者 choices/message/content 为空的响应对象。
    # 这里统一做结构校验，避免出现 `NoneType is not subscriptable`
    # 这类底层属性访问错误。
    choices = getattr(response, "choices", None)
    if not choices:
        raise ValueError(f"[{llm_provider}] returned empty choices")

    first_choice = choices[0]
    message = getattr(first_choice, "message", None)
    if message is None:
        raise ValueError(f"[{llm_provider}] returned empty message")

    content = getattr(message, "content", None)
    return _normalize_text_response(content, llm_provider)


def _get_response_field(value, key: str):
    """兼容 dict 和 SDK 响应对象的字段读取。"""
    if isinstance(value, dict):
        return value.get(key)

    try:
        return value[key]
    except (KeyError, TypeError, AttributeError):
        return getattr(value, key, None)


def _extract_qwen_generation_text(response) -> str:
    """
    从 DashScope Generation 响应中提取文本。

    Qwen 使用 `messages` 调用时返回的是 chat 结构：
    `output.choices[0].message.content`；旧 completion 形态才会返回
    `output.text`。这里两个路径都兼容，避免 `output.text` 为 None 时
    继续 `.replace()` 触发不可诊断的 AttributeError。
    """
    output = _get_response_field(response, "output")
    choices = _get_response_field(output, "choices") if output else None
    if choices is not None:
        if not choices:
            logger.warning("Qwen returned an empty choices list")
            raise ValueError("[qwen] returned empty choices")

        first_choice = choices[0]
        message = _get_response_field(first_choice, "message")
        content = _get_response_field(message, "content") if message else None
        if content is not None:
            return _normalize_text_response(content, "qwen")

    text = _get_response_field(output, "text") if output else None
    return _normalize_text_response(text, "qwen")


def _invoke_single_llm_provider(
    llm_provider: str,
    api_key: Optional[str] = None,
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
    prompt: str = ""
) -> str:
    """Tek bir LLM sağlayıcısına doğrudan istek atar."""
    provider_id_clean = str(llm_provider or DEFAULT_LLM_PROVIDER_ID).lower().strip()
    provider = get_llm_provider(provider_id_clean)
    if provider is None:
        raise ValueError(f"{provider_id_clean}: unsupported llm provider")

    resolved_api_key = api_key if (api_key is not None and api_key != "") else config.app.get(provider.config_key("api_key"), "")
    configured_model = model_name if (model_name is not None and model_name != "") else config.app.get(provider.config_key("model_name"), "")
    resolved_model = provider.resolve_model_name(configured_model)
    if configured_model and resolved_model != configured_model:
        logger.warning(
            f"{provider_id_clean} model '{configured_model}' is deprecated, fallback to '{resolved_model}'"
        )

    configured_base_url = base_url if (base_url is not None and base_url != "") else config.app.get(provider.config_key("base_url"), "")
    resolved_base_url = provider.resolve_base_url(configured_base_url)
    if configured_base_url and configured_base_url.strip().rstrip("/") in {
        url.rstrip("/") for url in provider.deprecated_base_urls
    }:
        logger.warning(
            f"{provider_id_clean} base URL '{configured_base_url}' is deprecated, fallback to '{resolved_base_url}'"
        )

    adapter = provider.adapter
    api_version = ""

    if provider_id_clean == "ollama":
        resolved_api_key = "ollama"
        if not resolved_base_url:
            resolved_base_url = config.get_default_ollama_base_url()

    if adapter == "azure":
        api_version = config.app.get(
            provider.config_key("api_version"), "2024-02-15-preview"
        )

    extra_values = {
        field.config_suffix: (
            config.app.get(provider.config_key(field.config_suffix), "")
            or field.default_value
        )
        for field in provider.extra_fields
    }

    if provider.requires_api_key and not resolved_api_key:
        raise ValueError(
            f"{provider_id_clean}: api_key is not set, please set it in the config or UI."
        )
    if provider.requires_model_name and not resolved_model:
        raise ValueError(
            f"{provider_id_clean}: model_name is not set, please set it in the config or UI."
        )
    if provider.requires_base_url and not resolved_base_url:
        raise ValueError(
            f"{provider_id_clean}: base_url is not set, please set it in the config or UI."
        )

    for field in provider.extra_fields:
        if field.required and not extra_values[field.config_suffix]:
            raise ValueError(
                f"{provider_id_clean}: {field.config_suffix} is not set, please set it in the config."
            )

    if adapter == "qwen":
        import dashscope
        from dashscope.api_entities.dashscope_response import GenerationResponse

        dashscope.api_key = resolved_api_key
        response = dashscope.Generation.call(
            model=resolved_model, messages=[{"role": "user", "content": prompt}]
        )
        if response:
            if isinstance(response, GenerationResponse):
                status_code = response.status_code
                if status_code != 200:
                    raise Exception(
                        f'[{provider_id_clean}] returned an error response: "{response}"'
                    )
                return _extract_qwen_generation_text(response)
            else:
                raise Exception(
                    f'[{provider_id_clean}] returned an invalid response: "{response}"'
                )
        else:
            raise Exception(f"[{provider_id_clean}] returned an empty response")

    if adapter == "gemini":
        from google import genai
        from google.genai import types

        http_options = types.HttpOptions(base_url=resolved_base_url) if resolved_base_url else None
        generation_config = types.GenerateContentConfig(
            temperature=0.5,
            top_p=1,
            top_k=1,
            max_output_tokens=2048,
            safety_settings=[
                types.SafetySetting(
                    category="HARM_CATEGORY_HARASSMENT",
                    threshold="BLOCK_ONLY_HIGH",
                ),
                types.SafetySetting(
                    category="HARM_CATEGORY_HATE_SPEECH",
                    threshold="BLOCK_ONLY_HIGH",
                ),
                types.SafetySetting(
                    category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    threshold="BLOCK_ONLY_HIGH",
                ),
                types.SafetySetting(
                    category="HARM_CATEGORY_DANGEROUS_CONTENT",
                    threshold="BLOCK_ONLY_HIGH",
                ),
            ],
        )

        try:
            with genai.Client(
                api_key=resolved_api_key,
                http_options=http_options,
            ) as client:
                response = client.models.generate_content(
                    model=resolved_model,
                    contents=prompt,
                    config=generation_config,
                )
            generated_text = response.text
        except (AttributeError, IndexError, ValueError) as e:
            logger.warning(f"gemini returned invalid response content: {str(e)}")
            raise ValueError(f"[{provider_id_clean}] returned invalid response content")

        return _normalize_text_response(generated_text, provider_id_clean)

    if adapter == "cloudflare_ai_gateway":
        account_id = extra_values["account_id"]
        gateway_id = extra_values["gateway_id"]
        client = OpenAI(
            api_key=resolved_api_key,
            base_url=(
                f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"
            ),
            default_headers={"cf-aig-gateway-id": gateway_id},
        )
        response = client.chat.completions.create(
            model=resolved_model,
            messages=[{"role": "user", "content": prompt}],
        )
        return _extract_chat_completion_text(response, provider_id_clean)

    if adapter == "litellm":
        import litellm

        if not resolved_model:
            raise ValueError(
                f"{provider_id_clean}: model_name is not set, please set it in the config or UI."
            )

        response = litellm.completion(
            model=resolved_model,
            messages=[{"role": "user", "content": prompt}],
            drop_params=True,
        )

        if not response or not getattr(response, "choices", None):
            raise ValueError(f"[{provider_id_clean}] returned empty response")

        return _extract_chat_completion_text(response, provider_id_clean)

    if adapter == "azure":
        logger.info(f"requesting azure chat completion, model: {resolved_model}")
        client = AzureOpenAI(
            api_key=resolved_api_key,
            api_version=api_version,
            azure_endpoint=resolved_base_url,
        )
        response = client.chat.completions.create(
            model=resolved_model, messages=[{"role": "user", "content": prompt}]
        )
        if response and isinstance(response, ChatCompletion):
            return _extract_chat_completion_text(response, provider_id_clean)
        else:
            raise Exception(f'[{provider_id_clean}] returned invalid response from Azure')

    if adapter == "modelscope":
        content = ""
        client = OpenAI(
            api_key=resolved_api_key,
            base_url=resolved_base_url,
        )
        response = client.chat.completions.create(
            model=resolved_model,
            messages=[{"role": "user", "content": prompt}],
            extra_body={"enable_thinking": False},
            stream=True,
        )
        if response:
            for chunk in response:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    content += delta.content

            if not content.strip():
                raise ValueError("Empty content in stream response")
            return _normalize_text_response(content, provider_id_clean)
        else:
            raise Exception(f"[{provider_id_clean}] returned an empty response")

    # Standart OpenAI Compatible (OpenAI, Groq, DeepSeek, Ollama, Moonshot vb.)
    client = OpenAI(
        api_key=resolved_api_key,
        base_url=resolved_base_url,
    )
    response = client.chat.completions.create(
        model=resolved_model, messages=[{"role": "user", "content": prompt}]
    )
    if response and isinstance(response, ChatCompletion):
        return _extract_chat_completion_text(response, provider_id_clean)
    else:
        raise Exception(f'[{provider_id_clean}] returned an invalid response')


def get_configured_llm_chain() -> list[dict]:
    """
    Kullanıcının tanımladığı LLM hiyerarşisini (Main, Fallback 1, Fallback 2) sırayla döndürür.
    """
    chain = []

    # 1. Main LLM
    main_provider = str(config.app.get("llm_provider", DEFAULT_LLM_PROVIDER_ID)).lower().strip()
    if main_provider:
        spec = get_llm_provider(main_provider)
        key = config.app.get(spec.config_key("api_key"), "") if spec else ""
        model = config.app.get(spec.config_key("model_name"), "") if spec else ""
        url = config.app.get(spec.config_key("base_url"), "") if spec else ""
        chain.append({
            "tier": "main",
            "label": "Ana AI (Main LLM)",
            "provider": main_provider,
            "api_key": key,
            "model_name": model,
            "base_url": url
        })

    # 2. Fallback 1 LLM
    fb1_provider = str(config.app.get("fallback_llm_provider", "")).lower().strip()
    if fb1_provider and fb1_provider not in ("none", "disabled", "devre_disi", "devre dışı", ""):
        fb1_key = config.app.get("fallback_llm_api_key", "")
        spec1 = get_llm_provider(fb1_provider)
        if not fb1_key and spec1:
            fb1_key = config.app.get(spec1.config_key("api_key"), "")
        fb1_model = config.app.get("fallback_llm_model_name", "")
        if not fb1_model and spec1:
            fb1_model = config.app.get(spec1.config_key("model_name"), "")
        fb1_url = config.app.get("fallback_llm_base_url", "")
        if not fb1_url and spec1:
            fb1_url = config.app.get(spec1.config_key("base_url"), "")
        chain.append({
            "tier": "fallback_1",
            "label": "1. Yedek AI (Fallback 1)",
            "provider": fb1_provider,
            "api_key": fb1_key,
            "model_name": fb1_model,
            "base_url": fb1_url
        })

    # 3. Fallback 2 LLM
    fb2_provider = str(config.app.get("fallback2_llm_provider", "")).lower().strip()
    if fb2_provider and fb2_provider not in ("none", "disabled", "devre_disi", "devre dışı", ""):
        fb2_key = config.app.get("fallback2_llm_api_key", "")
        spec2 = get_llm_provider(fb2_provider)
        if not fb2_key and spec2:
            fb2_key = config.app.get(spec2.config_key("api_key"), "")
        fb2_model = config.app.get("fallback2_llm_model_name", "")
        if not fb2_model and spec2:
            fb2_model = config.app.get(spec2.config_key("model_name"), "")
        fb2_url = config.app.get("fallback2_llm_base_url", "")
        if not fb2_url and spec2:
            fb2_url = config.app.get(spec2.config_key("base_url"), "")
        chain.append({
            "tier": "fallback_2",
            "label": "2. Yedek AI (Fallback 2)",
            "provider": fb2_provider,
            "api_key": fb2_key,
            "model_name": fb2_model,
            "base_url": fb2_url
        })

    return chain


def _generate_response(prompt: str) -> str:
    """
    Main LLM -> Fallback 1 -> Fallback 2 zincirini calistirir.
    Hata veya kota asiminda (429 vb.) bir sonraki saglayiciya otomatik gecer.
    """
    chain = get_configured_llm_chain()
    if not chain:
        raise LLMGenerationError("No LLM provider configured.")

    attempt_errors = []
    for idx, slot in enumerate(chain):
        tier_label = slot["label"]
        provider_id = slot["provider"]
        try:
            logger.info(f"[{tier_label}] Requesting completion from '{provider_id}' (model: '{slot['model_name'] or 'default'}')...")
            resp = _invoke_single_llm_provider(
                llm_provider=provider_id,
                api_key=slot["api_key"] or None,
                model_name=slot["model_name"] or None,
                base_url=slot["base_url"] or None,
                prompt=prompt
            )
            if resp:
                if idx > 0:
                    logger.success(f"[{tier_label}] Fallback successful using '{provider_id}' after previous tier failed!")
                return resp
            raise ValueError(f"Empty response received from {provider_id}")
        except Exception as e:
            sanitized_err = _sanitize_error_message(e)
            logger.warning(f"[{tier_label}] '{provider_id}' call failed: {sanitized_err}")
            attempt_errors.append(f"{tier_label} ({provider_id}): {sanitized_err}")
            if idx < len(chain) - 1:
                next_slot = chain[idx + 1]
                logger.info(f"Failing over to {next_slot['label']} ('{next_slot['provider']}')...")

    final_err = " | ".join(attempt_errors)
    logger.error(f"All configured LLM providers in fallback chain failed: {final_err}")
    raise LLMGenerationError(f"All configured LLM providers failed: {final_err}")


def test_connection(
    provider_id: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model_name: Optional[str] = None,
) -> tuple[bool, str, float]:
    """
    LLM bağlantısını test eder. Belirli parametreler verilirse o sağlayıcıyı;
    boş verilirse varsayılan zincir/ana modeli test eder.
    """
    started_at = perf_counter()
    if not provider_id and not api_key and not model_name and not base_url:
        try:
            response = _generate_response(prompt="Reply with exactly: OK")
        except LLMGenerationError as e:
            elapsed = perf_counter() - started_at
            logger.warning(f"llm connection test failed: {e}")
            return False, str(e), elapsed

        elapsed = perf_counter() - started_at
        if not response:
            error_message = "LLM returned an empty response"
            logger.warning(f"llm connection test failed: {error_message}")
            return False, error_message, elapsed
        if response.startswith("Error:"):
            err = response.replace("Error:", "").strip()
            logger.warning(f"llm connection test failed: {err}")
            return False, err, elapsed
        logger.info(f"llm connection test succeeded, elapsed: {elapsed:.2f}s")
        return True, "", elapsed

    target_provider = (provider_id or config.app.get("llm_provider", DEFAULT_LLM_PROVIDER_ID)).lower().strip()
    try:
        response = _invoke_single_llm_provider(
            llm_provider=target_provider,
            api_key=api_key,
            model_name=model_name,
            base_url=base_url,
            prompt="Reply with exactly: OK"
        )
    except Exception as e:
        elapsed = perf_counter() - started_at
        err = _sanitize_error_message(e)
        logger.warning(f"llm connection test failed for {target_provider}: {err}")
        return False, str(err), elapsed

    elapsed = perf_counter() - started_at
    if not response:
        return False, "LLM returned an empty response", elapsed
    if response.startswith("Error:"):
        return False, response.replace("Error:", "").strip(), elapsed

    logger.info(f"llm connection test succeeded for {target_provider}, elapsed: {elapsed:.2f}s")
    return True, "", elapsed


def _limit_script_text(text: str | None, max_length: int, field_name: str) -> str:
    value = (text or "").strip()
    if len(value) <= max_length:
        return value

    # API 层已经用 Pydantic 做长度校验；这里继续兜底，是为了保护
    # WebUI 或内部服务直接调用 generate_script 时不会把超长提示词发送给模型，
    # 避免 token 成本异常和请求失败。
    logger.warning(
        f"{field_name} is too long and will be truncated to {max_length} characters."
    )
    return value[:max_length]


def _normalize_script_paragraph_number(paragraph_number: int | None) -> int:
    try:
        value = int(paragraph_number or MIN_SCRIPT_PARAGRAPH_NUMBER)
    except (TypeError, ValueError):
        value = MIN_SCRIPT_PARAGRAPH_NUMBER

    if value < MIN_SCRIPT_PARAGRAPH_NUMBER or value > MAX_SCRIPT_PARAGRAPH_NUMBER:
        # WebUI 和 API 都会限制范围；这里兜底处理内部调用，避免异常参数直接扩大
        # LLM 生成成本或生成空结果。
        logger.warning(
            f"script paragraph_number is out of range and will be clamped: {value}"
        )
        return max(MIN_SCRIPT_PARAGRAPH_NUMBER, min(value, MAX_SCRIPT_PARAGRAPH_NUMBER))

    return value


def build_script_prompt(
    video_subject: str,
    language: str = "",
    paragraph_number: int = 1,
    video_script_prompt: str = "",
    custom_system_prompt: str = "",
    video_aspect: str = "",
) -> str:
    paragraph_number = _normalize_script_paragraph_number(paragraph_number)
    video_script_prompt = _limit_script_text(
        video_script_prompt, MAX_SCRIPT_PROMPT_LENGTH, "video_script_prompt"
    )
    custom_system_prompt = _limit_script_text(
        custom_system_prompt, MAX_SCRIPT_SYSTEM_PROMPT_LENGTH, "custom_system_prompt"
    )

    # 将“脚本生成规则”和“运行时上下文”分开拼接。这样高级用户即使覆盖默认
    # system prompt，也不会漏掉视频主题、语言、段落数这些每次生成都必须带上的参数。
    prompt = custom_system_prompt or DEFAULT_SCRIPT_SYSTEM_PROMPT
    prompt += f"""

# Initialization:
- video subject: <user_input>{video_subject}</user_input>
- number of paragraphs: {paragraph_number}
""".rstrip()
    if language:
        prompt += f"\n- language: {language}"
    if video_script_prompt:
        prompt += f"""

# Additional User Requirements:
<user_input>{video_script_prompt}</user_input>
""".rstrip()

    is_landscape = "16:9" in str(video_aspect) or "landscape" in str(video_aspect).lower()
    is_shorts = not is_landscape if video_aspect else True
    if is_shorts:
        prompt += """

# Viral Retention Hook Directive (YouTube Shorts / TikTok / Reels):
- The FIRST 3 SECONDS are decisive: The very first sentence MUST be an irresistible pattern-interrupt or curiosity hook that prevents users from swiping away.
- NEVER start with greetings, introductions, or slow filler (NO "Hello", "Welcome", "In this video", "Today we are").
- Start directly with a shocking fact, counter-intuitive claim, or high-stakes question in the target language.
""".rstrip()

    return prompt


def generate_script(
    video_subject: str,
    language: str = "",
    paragraph_number: int = 1,
    video_script_prompt: str = "",
    custom_system_prompt: str = "",
    video_aspect: str = "",
) -> str:
    paragraph_number = _normalize_script_paragraph_number(paragraph_number)
    video_script_prompt = _limit_script_text(
        video_script_prompt, MAX_SCRIPT_PROMPT_LENGTH, "video_script_prompt"
    )
    custom_system_prompt = _limit_script_text(
        custom_system_prompt, MAX_SCRIPT_SYSTEM_PROMPT_LENGTH, "custom_system_prompt"
    )
    prompt = build_script_prompt(
        video_subject=video_subject,
        language=language,
        paragraph_number=paragraph_number,
        video_script_prompt=video_script_prompt,
        custom_system_prompt=custom_system_prompt,
        video_aspect=video_aspect,
    )
    final_script = ""
    logger.info(
        "generating video script: "
        f"subject={video_subject}, paragraph_number={paragraph_number}, aspect={video_aspect}, "
        f"has_custom_prompt={bool(video_script_prompt.strip())}, "
        f"has_custom_system_prompt={bool(custom_system_prompt.strip())}"
    )

    def format_response(response):
        # Clean the script
        # Remove asterisks, hashes
        response = response.replace("*", "")
        response = response.replace("#", "")

        # Remove markdown syntax
        response = re.sub(r"\[.*\]", "", response)
        response = re.sub(r"\(.*\)", "", response)

        # Split the script into paragraphs
        paragraphs = response.split("\n\n")

        # Select the specified number of paragraphs
        # selected_paragraphs = paragraphs[:paragraph_number]

        # Join the selected paragraphs into a single string
        return "\n\n".join(paragraphs)

    last_error = None
    for i in range(_max_retries):
        try:
            response = _generate_response(prompt=prompt)
            if response:
                final_script = format_response(response)
            else:
                logging.error("gpt returned an empty response")

            if final_script and "当日额度已消耗完" in final_script:
                raise LLMGenerationError(final_script)

            if final_script:
                break
        except LLMGenerationError as e:
            last_error = e
            logger.error(f"failed to generate script: {e}")
        except Exception as e:
            last_error = LLMGenerationError(str(e))
            logger.error(f"failed to generate script: {e}")

        if i < _max_retries - 1:
            logger.warning(f"failed to generate video script, trying again... {i + 1}")

    if not final_script and last_error:
        raise last_error
    elif not final_script:
        raise LLMGenerationError("Failed to generate video script")

    logger.success(f"completed: \n{final_script}")
    return final_script.strip()


def _strip_code_fence(text: str) -> str:
    """Strip a surrounding markdown code fence from an LLM response.

    Non-OpenAI providers (Claude, Gemini, …) frequently wrap JSON output in a
    ```json … ``` fence even when asked to return raw JSON. Removing it lets the
    first json.loads() succeed instead of falling through to the regex recovery
    path (and spuriously logging a warning). Mirrors the DOTALL handling already
    used in _parse_social_metadata().
    """
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def generate_terms(
    video_subject: str,
    video_script: str,
    amount: int = 5,
    match_script_order: bool = False,
) -> List[str]:
    if match_script_order:
        goal = (
            f"Generate {amount} chronological stock-video search terms that follow "
            "the order of topics in the video script."
        )
        ordering_rule = (
            "6. keep the terms in the same order as the script narration; "
            "earlier terms must describe earlier visual moments."
        )
        # 有序关键词模式下，示例数量要和 amount 保持一致，避免模型被固定
        # 的 4 个示例误导，导致长文案只返回少量关键词，影响素材覆盖度。
        example_terms = [
            "opening visual topic",
            *[f"script visual topic {index}" for index in range(2, max(amount, 1))],
            "final visual topic",
        ]
        output_example = json.dumps(example_terms[:amount], ensure_ascii=False)
    else:
        goal = (
            f"Generate {amount} search terms for stock videos, depending on the "
            "subject of a video."
        )
        ordering_rule = ""
        output_example = (
            '["search term 1", "search term 2", "search term 3",'
            '"search term 4", "search term 5"]'
        )

    prompt = f"""
# Role: Video Search Terms Generator

## Goals:
{goal}

## Constrains:
1. the search terms are to be returned as a json-array of strings.
2. each search term should consist of 1-3 words, always add the main subject of the video.
3. you must only return the json-array of strings. you must not return anything else. you must not return the script.
4. the search terms must be related to the subject of the video.
5. reply with english search terms only.
{ordering_rule}

## Output Example:
{output_example}

## Context:
### Video Subject
{video_subject}

### Video Script
{video_script}

Please note that you must use English for generating video search terms; Chinese is not accepted.
""".strip()

    logger.info(f"subject: {video_subject}, match_script_order: {match_script_order}")

    search_terms = []
    response = ""
    last_error = None
    for i in range(_max_retries):
        try:
            response = _generate_response(prompt)
            if response and response.startswith("Error:"):
                logger.warning(f"provider returned error in generate_terms: {response}")
                return []
            search_terms = json.loads(_strip_code_fence(response))
            if not isinstance(search_terms, list) or not all(
                isinstance(term, str) for term in search_terms
            ):
                logger.error("response is not a list of strings.")
                search_terms = []
                last_error = LLMGenerationError("response is not a list of strings")
                continue

        except LLMGenerationError as e:
            last_error = e
            logger.warning(f"failed to generate video terms: {str(e)}")
        except Exception as e:
            last_error = LLMGenerationError(str(e))
            logger.warning(f"failed to generate video terms: {str(e)}")
            if response:
                match = re.search(r"\[.*]", response, re.DOTALL)
                if match:
                    try:
                        recovered = json.loads(match.group())
                        if isinstance(recovered, list) and all(
                            isinstance(term, str) for term in recovered
                        ):
                            search_terms = recovered
                            last_error = None
                    except Exception as parse_e:
                        # 这里保留重试流程，但必须记录 LLM 返回的非标准 JSON，
                        # 否则后续排查搜索词为空时无法定位
                        # 是模型格式问题还是解析逻辑问题。
                        logger.warning(f"failed to generate video terms: {str(parse_e)}")

        if search_terms and len(search_terms) > 0:
            break
        if i < _max_retries - 1:
            logger.warning(f"failed to generate video terms, trying again... {i + 1}")

    if not search_terms and last_error:
        raise last_error
    elif not search_terms:
        raise LLMGenerationError("Failed to generate video terms")

    logger.success(f"completed: \n{search_terms}")
    return search_terms


# =============================================================================
# Social publishing metadata
#
# 根据视频主题和脚本生成发布到短视频平台时常用的 title、caption 和 hashtags。
# 这块能力只复用现有 LLM provider，不接入任何外部发布服务，也不影响视频生成主链路。
# =============================================================================

# 不同平台的文案长度和 hashtag 数量偏好不同。这里使用保守上限，避免模型返回
# 过长内容后调用方还需要二次裁剪。
SOCIAL_PLATFORMS = {
    "tiktok": {"title_max": 100, "caption_max": 2200, "hashtag_count": 5},
    "youtube_shorts": {"title_max": 100, "caption_max": 5000, "hashtag_count": 3},
    "youtube_video": {"title_max": 100, "caption_max": 5000, "hashtag_count": 5},
    "instagram_reels": {"title_max": 125, "caption_max": 2200, "hashtag_count": 8},
    "facebook_reels": {"title_max": 125, "caption_max": 2200, "hashtag_count": 5},
}
DEFAULT_SOCIAL_PLATFORM = "tiktok"
DEFAULT_SOCIAL_LANGUAGE = "auto"
MAX_SOCIAL_SUBJECT_LENGTH = 500
MAX_SOCIAL_SCRIPT_LENGTH = 8000
MAX_SOCIAL_LANGUAGE_LENGTH = 64

SOCIAL_PLATFORM_LABELS = {
    "tiktok": "TikTok",
    "youtube_shorts": "YouTube Shorts",
    "youtube_video": "YouTube Video",
    "instagram_reels": "Instagram Reels",
    "facebook_reels": "Facebook Reels",
}

# LLM 不可用时的通用兜底标签。这里故意不绑定某个国家或语种，保证 API
# 对中文、英文、越南语等不同场景都能返回可用结构。
DEFAULT_SOCIAL_HASHTAGS = [
    "#shorts",
    "#viral",
    "#trending",
    "#fyp",
    "#video",
    "#reels",
    "#creator",
    "#content",
]


def _resolve_social_platform(platform: str | None) -> str:
    value = (platform or "").strip().lower()
    return value if value in SOCIAL_PLATFORMS else DEFAULT_SOCIAL_PLATFORM


def _normalize_social_language(language: str | None) -> str:
    value = (language or DEFAULT_SOCIAL_LANGUAGE).strip()
    if len(value) > MAX_SOCIAL_LANGUAGE_LENGTH:
        logger.warning(
            "social metadata language is too long and will be truncated to "
            f"{MAX_SOCIAL_LANGUAGE_LENGTH} characters."
        )
        value = value[:MAX_SOCIAL_LANGUAGE_LENGTH]
    return value or DEFAULT_SOCIAL_LANGUAGE


def _limit_social_text(text: str | None, max_length: int, field_name: str) -> str:
    value = (text or "").strip()
    if len(value) <= max_length:
        return value

    # API 层会限制长度；这里继续兜底，是为了保护内部调用或未来 WebUI
    # 直接调用时不会把超长内容发送给模型，避免 token 成本异常。
    logger.warning(
        f"{field_name} is too long and will be truncated to {max_length} characters."
    )
    return value[:max_length]


def _social_language_instruction(language: str | None) -> str:
    language = _normalize_social_language(language)
    if language.lower() == DEFAULT_SOCIAL_LANGUAGE:
        return (
            "Use the same language as the video subject and script. If the subject "
            "and script use different languages, prefer the script language."
        )

    return f'Write "title" and "caption" in this language: {language}.'


def _clamp_text(text, max_length: int) -> str:
    value = ("" if text is None else str(text)).strip()
    if max_length and len(value) > max_length:
        return value[:max_length].rstrip()
    return value


def _normalize_hashtags(raw, count: int) -> List[str]:
    """
    将 LLM 返回的 hashtag 统一整理成 `#tag` 格式。

    LLM 可能返回字符串、数组、带空格的词组、重复标签或包含标点的内容。
    这里集中清洗，可以让接口响应结构稳定，也避免平台发布时出现空标签、
    重复标签或不符合常见格式的 hashtag。
    """
    if isinstance(raw, str):
        candidates = re.split(r"[\s,]+", raw)
    elif isinstance(raw, (list, tuple)):
        # 数组里的每一项视为一个完整标签，因此 "du lich" 会变成
        # "#dulich"，而不是拆成两个标签。
        candidates = [str(entry) for entry in raw]
    else:
        candidates = []

    seen = set()
    result: List[str] = []
    for item in candidates:
        tag = re.sub(r"[^\w]", "", item, flags=re.UNICODE)
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(f"#{tag}")
        if count and len(result) >= count:
            break
    return result


def build_social_metadata_prompt(
    video_subject: str,
    video_script: str = "",
    language: str = DEFAULT_SOCIAL_LANGUAGE,
    platform: str = DEFAULT_SOCIAL_PLATFORM,
) -> str:
    video_subject = _limit_social_text(
        video_subject, MAX_SOCIAL_SUBJECT_LENGTH, "video_subject"
    )
    video_script = _limit_social_text(
        video_script, MAX_SOCIAL_SCRIPT_LENGTH, "video_script"
    )
    platform = _resolve_social_platform(platform)
    spec = SOCIAL_PLATFORMS[platform]
    label = SOCIAL_PLATFORM_LABELS.get(platform, platform)
    language_instruction = _social_language_instruction(language)

    prompt = f"""
# Role: Short-Video Social Media Copywriter

## Goal
Write engaging publishing metadata for a short video that will be posted on {label}.

## Constraints
1. Respond ONLY with a single valid minified JSON object. No markdown, no code fences, no commentary.
2. The JSON must contain exactly these keys: "title", "caption", "hashtags".
3. "title": a catchy hook, at most {spec["title_max"]} characters.
4. "caption": an engaging description that ends with a call to action, at most {spec["caption_max"]} characters. Do not put hashtags inside the caption.
5. "hashtags": a JSON array of exactly {spec["hashtag_count"]} strings. Each must start with "#", contain no spaces, and be relevant to the topic and to {label}.
6. {language_instruction}

## Output Example
{{"title":"...","caption":"...","hashtags":["#example","#video"]}}

## Context
### Video Subject
{video_subject}

### Video Script
{video_script}
""".strip()
    return prompt


def _parse_social_metadata(response: str, platform: str) -> dict:
    spec = SOCIAL_PLATFORMS[_resolve_social_platform(platform)]

    data = None
    try:
        data = json.loads(_strip_code_fence(response))
    except Exception:
        # 部分模型会在 JSON 外层包一段说明文字或 markdown fence。
        # API 调用方只需要稳定结构，所以这里尝试提取第一个 JSON object。
        match = re.search(r"\{.*\}", response or "", re.DOTALL)
        if match:
            data = json.loads(match.group())

    if not isinstance(data, dict):
        raise ValueError("social metadata response is not a JSON object")

    title = _clamp_text(data.get("title", ""), spec["title_max"])
    caption = _clamp_text(data.get("caption", ""), spec["caption_max"])
    hashtags = _normalize_hashtags(data.get("hashtags", []), spec["hashtag_count"])

    if not title and not caption:
        raise ValueError("social metadata response is missing both title and caption")

    return {"title": title, "caption": caption, "hashtags": hashtags}


def _fallback_social_metadata(
    video_subject: str, video_script: str, platform: str
) -> dict:
    spec = SOCIAL_PLATFORMS[_resolve_social_platform(platform)]
    subject = (video_subject or "").strip()
    script = (video_script or "").strip()

    title = subject
    if not title and script:
        # 没有主题时，用脚本第一句兜底生成 title，避免接口返回空标题。
        title = re.split(r"(?<=[.!?。！？])\s+", script)[0]

    return {
        "title": _clamp_text(title, spec["title_max"]),
        "caption": _clamp_text(script or subject, spec["caption_max"]),
        "hashtags": _normalize_hashtags(DEFAULT_SOCIAL_HASHTAGS, spec["hashtag_count"]),
    }


def generate_social_metadata(
    video_subject: str,
    video_script: str = "",
    language: str = DEFAULT_SOCIAL_LANGUAGE,
    platform: str = DEFAULT_SOCIAL_PLATFORM,
) -> dict:
    """
    生成短视频发布文案元数据。

    返回结构固定为 `{"title": str, "caption": str, "hashtags": List[str]}`。
    如果 LLM 不可用或返回格式异常，会降级为通用启发式结果，保证 API
    调用方始终拿到可展示、可发布前编辑的数据结构。
    """
    platform = _resolve_social_platform(platform)
    language = _normalize_social_language(language)
    video_subject = _limit_social_text(
        video_subject, MAX_SOCIAL_SUBJECT_LENGTH, "video_subject"
    )
    video_script = _limit_social_text(
        video_script, MAX_SOCIAL_SCRIPT_LENGTH, "video_script"
    )
    prompt = build_social_metadata_prompt(
        video_subject=video_subject,
        video_script=video_script,
        language=language,
        platform=platform,
    )
    logger.info(f"generating social metadata: platform={platform}, language={language}")

    response = ""
    last_error = None
    for i in range(_max_retries):
        try:
            response = _generate_response(prompt)
            metadata = _parse_social_metadata(response, platform)
            logger.success(f"completed: \n{metadata}")
            return metadata
        except LLMGenerationError as e:
            last_error = e
            logger.warning(f"failed to generate social metadata: {str(e)}")
        except Exception as e:
            last_error = LLMGenerationError(str(e))
            logger.warning(f"failed to parse social metadata: {str(e)}")

        if i < _max_retries - 1:
            logger.warning(
                f"failed to generate social metadata, trying again... {i + 1}"
            )

    if last_error:
        logger.warning(
            f"failed to generate social metadata via LLM ({last_error}), falling back to heuristic metadata"
        )
        return _fallback_social_metadata(video_subject, video_script, platform)

    return _fallback_social_metadata(video_subject, video_script, platform)


# ================= OpenShorts Viral Moment Secimi =================

def _fallback_viral_moment(total_duration: float, video_title: str = "", channel_name: str = "") -> dict:
    mid = max(0.0, (total_duration - 40.0) / 2.0)
    hook = f"{channel_name}: {video_title}" if channel_name else video_title
    if not hook:
        hook = "En Çarpıcı An 💥"
    else:
        hook = f"{hook[:45]} 💥"
    return {
        "start_sec": round(mid, 2),
        "end_sec": round(min(total_duration, mid + 40.0), 2),
        "viral_hook_text": hook,
        "youtube_title": f"{hook} #shorts",
        "viral_score": 7,
        "reason": "Heuristic fallback",
        "is_fallback": True
    }


def generate_viral_moment_selection(
    transcript_with_timestamps: str,
    total_duration: float,
    channel_name: str = "",
    video_title: str = "",
    min_window: float = 25.0,
    max_window: float = 55.0,
) -> dict:
    """
    OpenShorts transkript pencereli viral an analizi:
    LLM'i kullanarak transkriptten izleyiciyi en cok tutacak 25-55 saniyelik bolumu,
    dikkat cekici kanca (hook) metnini ve YouTube Shorts basligini tespit eder.
    """
    if not transcript_with_timestamps or not transcript_with_timestamps.strip():
        return _fallback_viral_moment(total_duration, video_title, channel_name)

    prompt = f"""
Sen YouTube Shorts, TikTok ve Instagram Reels alaninda uzmanlasmis viral bir video editorusun.
Asagida bir yayinciya veya videoya ait zaman damgali transkript yer aliyor.

Video Basligi: {video_title or 'Bilinmiyor'}
Yayinci / Kanal: {channel_name or 'Bilinmiyor'}
Toplam Sure: {total_duration:.1f} saniye

ZAMAN DAMGALI TRANSKRIPT:
\"\"\"
{transcript_with_timestamps[:6000]}
\"\"\"

GOREVIN:
1. Bu transkript icindeki en komik, en heyecanli, merak uyandirici veya duygusal patlama yasanan {min_window:.0f}-{max_window:.0f} saniyelik TEK BIR viral anı sec.
2. Baslangic (start_sec) ve bitis (end_sec) saniyelerini tam sayisal deger olarak belirle (0 <= start_sec < end_sec <= {total_duration:.1f}).
3. Bu klibin tepesinde gorunecek, kullanicilarin kaydirmasini onleyecek 5-9 kelimelik carpici bir KANCA BASLIGI (viral_hook_text) yaz (1-2 emoji ekle, ornegin: "Rraenee sinir krizi geçirdi 💀" veya "Bunu hiç beklemiyordu 😱").
4. 80 karakteri asmayan, tiklama orani yuksek bir YouTube Shorts basligi yaz (youtube_title).
5. Klibe 1-10 arasi bir viral potansiyel puani ver (viral_score).

YALNIZCA asagidaki JSON formatinda yanit ver, baska hicbir aciklama yazma:
{{
  "start_sec": 12.5,
  "end_sec": 48.0,
  "viral_hook_text": "Kanca metni burada 🚀",
  "youtube_title": "YouTube Basligi #shorts",
  "viral_score": 9,
  "reason": "Neden viral olacagi"
}}
""".strip()

    logger.info(f"Analyzing transcript for viral moments via LLM (video_duration={total_duration:.1f}s)")
    for i in range(_max_retries):
        try:
            raw = _generate_response(prompt)
            cleaned = _strip_code_fence(raw)
            match = re.search(r"\{.*\}", cleaned or "", re.DOTALL)
            if match:
                data = json.loads(match.group())
                start_sec = float(data.get("start_sec", 0.0))
                end_sec = float(data.get("end_sec", min(total_duration, start_sec + 40.0)))
                # Sınır kontrolleri
                start_sec = max(0.0, min(start_sec, total_duration - 5.0))
                end_sec = min(total_duration, max(start_sec + 10.0, end_sec))
                hook = str(data.get("viral_hook_text", "")).strip() or (video_title[:45] + " 🔥")
                title = str(data.get("youtube_title", "")).strip() or f"{hook} #shorts"
                score = int(data.get("viral_score", 8))

                logger.success(f"Viral moment selected: {start_sec:.1f}s - {end_sec:.1f}s (Score: {score}) Hook: '{hook}'")
                return {
                    "start_sec": round(start_sec, 2),
                    "end_sec": round(end_sec, 2),
                    "viral_hook_text": hook,
                    "youtube_title": title,
                    "viral_score": score,
                    "reason": str(data.get("reason", ""))
                }
        except Exception as e:
            logger.warning(f"Failed to generate/parse viral moment on attempt {i+1}: {e}")

    logger.warning("LLM viral moment selection failed, falling back to heuristic")
    return _fallback_viral_moment(total_duration, video_title, channel_name)


if __name__ == "__main__":
    video_subject = "生命的意义是什么"
    script = generate_script(
        video_subject=video_subject, language="zh-CN", paragraph_number=1
    )
    print("######################")
    print(script)
    search_terms = generate_terms(
        video_subject=video_subject, video_script=script, amount=5
    )
    print("######################")
    print(search_terms)
