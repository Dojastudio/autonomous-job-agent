"""
Multi-Model Free-Tier AI Router with Automatic Failover, Exponential Backoff,
Strict JSON Validation, 384-d Embeddings, and Local PII Redaction/Re-injection.

Routing Tiers:
1. High-Context Parsing & Scoring (`cv_parser`, `job_scorer`):
   - Primary: Google AI Studio (`gemini-3.5-flash-lite` / `gemini-3.8-flash`)
   - Fallback: OpenRouter (`:free` models) -> Cerebras free tier
2. Low-Latency Form Mapping & Screening Q&A (`form_mapper`, `question_answerer`):
   - Primary: Groq (`openai/gpt-oss-120b` / `qwen/qwen3.8-27b`)
   - Fallback: Cerebras free tier -> Google AI Studio (`gemini-3.5-flash-lite`)
3. Semantic Vector Matching (`embeddings`):
   - Local `sentence-transformers` (`all-MiniLM-L6-v2`, 384 dims) OR Google AI Studio embedding
     normalized/truncated to 384 dims, with deterministic fallback for offline/test environments.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Type, TypeVar, Union
import urllib.error
import urllib.request

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    httpx = None  # type: ignore
    _HAS_HTTPX = False

try:
    from pydantic import BaseModel, ValidationError
    _HAS_PYDANTIC = True
except ImportError:
    _HAS_PYDANTIC = False

    class ValidationError(ValueError):  # type: ignore
        pass

    class BaseModel:  # type: ignore
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

        @classmethod
        def model_validate(cls, data: Dict[str, Any]) -> Any:
            annotations = getattr(cls, "__annotations__", {})
            for field_name in annotations:
                if field_name not in data and not hasattr(cls, field_name):
                    raise ValidationError(f"Missing required schema field: '{field_name}'")
            return cls(**data)

        def model_dump(self) -> Dict[str, Any]:
            return dict(self.__dict__)

from backend.core.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# ==============================================================================
# 1. PRIVACY GUARDRAIL: PII REDACTION & LOCAL RE-INJECTION
# ==============================================================================

EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_REGEX = re.compile(
    r"(?:(?:\+|00)\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d{3,4}[\s.-]?\d{3,4}\b"
)
STREET_ADDRESS_REGEX = re.compile(
    r"\b\d{1,5}\s+[A-Za-z0-9.\s]{3,35}\s+"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|Parkway|Pkwy)\b\.?",
    re.IGNORECASE,
)


@dataclass
class PIIRedactor:
    """
    Replaces direct candidate PII (`full_name`, `email`, `phone`, `street_address`)
    with deterministic placeholders (`{{CANDIDATE_NAME}}`, `{{EMAIL}}`, `{{PHONE}}`,
    `{{STREET_ADDRESS}}`, `{{FIRST_NAME}}`, `{{LAST_NAME}}`) before sending text or
    JSON payloads to external free-tier LLM APIs, and restores the real values
    locally right before Playwright fills DOM inputs.
    """

    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    street_address: Optional[str] = None
    placeholder_map: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.full_name and self.full_name.strip():
            clean_name = self.full_name.strip()
            self.placeholder_map["{{CANDIDATE_NAME}}"] = clean_name
            parts = clean_name.split()
            if len(parts) >= 1:
                self.placeholder_map["{{FIRST_NAME}}"] = parts[0]
            if len(parts) >= 2:
                self.placeholder_map["{{LAST_NAME}}"] = " ".join(parts[1:])

        if self.email and self.email.strip():
            self.placeholder_map["{{EMAIL}}"] = self.email.strip()

        if self.phone and self.phone.strip():
            self.placeholder_map["{{PHONE}}"] = self.phone.strip()

        if self.street_address and self.street_address.strip():
            self.placeholder_map["{{STREET_ADDRESS}}"] = self.street_address.strip()

    def redact_text(self, text: str) -> str:
        """
        Strips known PII values and regex-detected emails, phone numbers, and street addresses.
        Also records any newly discovered regex matches into `placeholder_map` so they can be
        re-injected later even if `candidate_profiles` didn't have them populated yet.
        """
        if not text:
            return text

        redacted = text

        # 1. Exact known value replacements first
        if "{{STREET_ADDRESS}}" in self.placeholder_map:
            val = self.placeholder_map["{{STREET_ADDRESS}}"]
            if val:
                redacted = re.sub(re.escape(val), "{{STREET_ADDRESS}}", redacted, flags=re.IGNORECASE)

        if "{{EMAIL}}" in self.placeholder_map:
            val = self.placeholder_map["{{EMAIL}}"]
            if val:
                redacted = re.sub(re.escape(val), "{{EMAIL}}", redacted, flags=re.IGNORECASE)

        if "{{PHONE}}" in self.placeholder_map:
            val = self.placeholder_map["{{PHONE}}"]
            if val:
                redacted = re.sub(re.escape(val), "{{PHONE}}", redacted)

        if "{{CANDIDATE_NAME}}" in self.placeholder_map:
            val = self.placeholder_map["{{CANDIDATE_NAME}}"]
            if len(val) > 2:
                redacted = re.sub(re.escape(val), "{{CANDIDATE_NAME}}", redacted, flags=re.IGNORECASE)

        # 2. Regex-based detection for emails
        email_matches = EMAIL_REGEX.findall(redacted)
        for match in email_matches:
            if "{{EMAIL}}" not in self.placeholder_map:
                self.placeholder_map["{{EMAIL}}"] = match
            redacted = redacted.replace(match, "{{EMAIL}}")

        # 3. Regex-based detection for street addresses
        addr_matches = STREET_ADDRESS_REGEX.findall(redacted)
        for match in addr_matches:
            if "{{STREET_ADDRESS}}" not in self.placeholder_map:
                self.placeholder_map["{{STREET_ADDRESS}}"] = match.strip()
            redacted = redacted.replace(match, "{{STREET_ADDRESS}}")

        # 4. Regex-based detection for phone numbers (minimum 7 digits to avoid years/zip codes)
        def _replace_phone(match_obj: re.Match) -> str:
            raw = match_obj.group(0)
            digits = re.sub(r"\D", "", raw)
            # Avoid redacting 4-digit years or ranges like 2020-2024
            if len(digits) < 10 or len(digits) > 15:
                return raw
            if re.match(r"^(19|20)\d{2}[\s.-]+(19|20)\d{2}$", raw.strip()):
                return raw
            if "{{PHONE}}" not in self.placeholder_map:
                self.placeholder_map["{{PHONE}}"] = raw.strip()
            return "{{PHONE}}"

        redacted = PHONE_REGEX.sub(_replace_phone, redacted)
        return redacted

    def redact_data(self, data: Any) -> Any:
        """Recursively redacts PII from dicts, lists, and strings."""
        if isinstance(data, str):
            return self.redact_text(data)
        if isinstance(data, dict):
            redacted_dict: Dict[str, Any] = {}
            for k, v in data.items():
                key_lower = k.lower()
                if key_lower in ("full_name", "name", "candidate_name") and isinstance(v, str) and v.strip():
                    if "{{CANDIDATE_NAME}}" not in self.placeholder_map:
                        self.placeholder_map["{{CANDIDATE_NAME}}"] = v.strip()
                    redacted_dict[k] = "{{CANDIDATE_NAME}}"
                elif key_lower in ("email", "email_address") and isinstance(v, str) and v.strip():
                    if "{{EMAIL}}" not in self.placeholder_map:
                        self.placeholder_map["{{EMAIL}}"] = v.strip()
                    redacted_dict[k] = "{{EMAIL}}"
                elif key_lower in ("phone", "phone_number", "mobile") and isinstance(v, str) and v.strip():
                    if "{{PHONE}}" not in self.placeholder_map:
                        self.placeholder_map["{{PHONE}}"] = v.strip()
                    redacted_dict[k] = "{{PHONE}}"
                elif key_lower in ("street_address", "address") and isinstance(v, str) and v.strip():
                    if "{{STREET_ADDRESS}}" not in self.placeholder_map:
                        self.placeholder_map["{{STREET_ADDRESS}}"] = v.strip()
                    redacted_dict[k] = "{{STREET_ADDRESS}}"
                else:
                    redacted_dict[k] = self.redact_data(v)
            return redacted_dict
        if isinstance(data, list):
            return [self.redact_data(item) for item in data]
        return data

    def reinject_text(self, text: str) -> str:
        """Restores all `{{CANDIDATE_NAME}}`, `{{EMAIL}}`, `{{PHONE}}`, etc. placeholders."""
        if not text or not isinstance(text, str):
            return text
        restored = text
        for placeholder, real_value in self.placeholder_map.items():
            if real_value:
                restored = restored.replace(placeholder, real_value)
        return restored

    def reinject_data(self, data: Any) -> Any:
        """Recursively restores PII placeholders across a dict/list/string payload."""
        if isinstance(data, str):
            return self.reinject_text(data)
        if isinstance(data, dict):
            return {k: self.reinject_data(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self.reinject_data(item) for item in data]
        return data


# ==============================================================================
# 2. PROVIDER ROUTE DEFINITIONS & EXCEPTIONS
# ==============================================================================

class RateLimitError(Exception):
    """Raised when a provider returns HTTP 429 or quota exhaustion."""


class ModelRouterError(Exception):
    """Raised when all providers in a task chain fail."""


@dataclass(frozen=True)
class ProviderEndpoint:
    provider: str  # "gemini", "groq", "openrouter", "cerebras"
    model: str
    base_url: Optional[str] = None
    api_key_attr: str = ""


_GEMINI = ProviderEndpoint('gemini',settings.GEMINI_MODEL,'https://generativelanguage.googleapis.com/v1beta/openai/','GEMINI_API_KEY')
_GROQ = ProviderEndpoint('groq',settings.GROQ_MODEL,'https://api.groq.com/openai/v1','GROQ_API_KEY')
_OPTIONAL = []
if settings.OPENROUTER_MODEL:
    _OPTIONAL.append(ProviderEndpoint('openrouter',settings.OPENROUTER_MODEL,'https://openrouter.ai/api/v1','OPENROUTER_API_KEY'))
if settings.CEREBRAS_MODEL:
    _OPTIONAL.append(ProviderEndpoint('cerebras',settings.CEREBRAS_MODEL,'https://api.cerebras.ai/v1','CEREBRAS_API_KEY'))
TASK_ROUTES = {
    'cv_parser': [_GEMINI,_GROQ]+_OPTIONAL,
    'job_scorer': [_GEMINI,_GROQ]+_OPTIONAL,
    'form_mapper': [_GROQ,_GEMINI]+_OPTIONAL,
    'question_answerer': [_GROQ,_GEMINI]+_OPTIONAL,
}


# ==============================================================================
# 3. JSON EXTRACTION & VALIDATION HELPERS
# ==============================================================================

def extract_clean_json(raw_text: str) -> Dict[str, Any]:
    """
    Parses a JSON object from LLM output, stripping markdown code fences or
    surrounding chatter if the model included any despite `json_object` mode.
    """
    if not raw_text or not raw_text.strip():
        raise ValueError("Empty response received from model.")

    cleaned = raw_text.strip()

    # Strip ```json ... ``` fences
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
        raise ValueError(f"Expected JSON object (dict), got {type(parsed).__name__}")
    except json.JSONDecodeError:
        # Attempt to locate the first '{' and last '}'
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            snippet = cleaned[first_brace : last_brace + 1]
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                return parsed
        raise


# ==============================================================================
# 4. UNIFIED MODEL ROUTER
# ==============================================================================

class ModelRouter:
    """
    Unified OpenAI-SDK-compatible Multi-Model Router with:
    - Task-specific primary & fallback provider chains
    - Exponential backoff on HTTP 429 Rate Limits
    - Strict JSON mode (`response_format={"type": "json_object"}`) + optional Pydantic validation
    - Automatic PII redaction before outbound API calls and optional local re-injection
    - 384-dim semantic vector embedding generation (`all-MiniLM-L6-v2` or Gemini / deterministic fallback)
    """

    def __init__(
        self,
        max_retries_per_endpoint: int = 3,
        base_backoff_seconds: float = 0.5,
        http_timeout_seconds: float = 30.0,
        custom_transport_handler: Optional[
            Callable[[ProviderEndpoint, List[Dict[str, str]], float], Awaitable[str]]
        ] = None,
    ) -> None:
        self.max_retries_per_endpoint = max_retries_per_endpoint
        self.base_backoff_seconds = base_backoff_seconds
        self.http_timeout_seconds = http_timeout_seconds
        self._custom_transport_handler = custom_transport_handler
        self._local_embedder: Any = None

    def _get_api_key(self, endpoint: ProviderEndpoint) -> str:
        return getattr(settings, endpoint.api_key_attr, "") or ""

    async def _call_endpoint_openai_compatible(
        self,
        endpoint: ProviderEndpoint,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
    ) -> str:
        """
        Executes a chat completion call with `response_format={"type": "json_object"}`
        against any OpenAI-compatible endpoint (Groq, Gemini OpenAI compat, OpenRouter, Cerebras).
        """
        if self._custom_transport_handler is not None:
            return await self._custom_transport_handler(endpoint, messages, temperature)

        api_key = self._get_api_key(endpoint)
        if not api_key:
            raise ModelRouterError(
                f"Missing API key `{endpoint.api_key_attr}` for provider `{endpoint.provider}`."
            )

        url = f"{endpoint.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": endpoint.model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }

        if _HAS_HTTPX and httpx is not None:
            async with httpx.AsyncClient(timeout=self.http_timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
            status_code = response.status_code
            response_text = response.text
        else:
            def _sync_post() -> Tuple[int, str]:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(req, timeout=self.http_timeout_seconds) as resp:
                        return resp.status, resp.read().decode("utf-8")
                except urllib.error.HTTPError as e:
                    return e.code, e.read().decode("utf-8", errors="replace")

            status_code, response_text = await asyncio.to_thread(_sync_post)

        if status_code == 429:
            raise RateLimitError(
                f"429 Rate Limit from {endpoint.provider}/{endpoint.model}: {response_text[:200]}"
            )

        if status_code >= 400:
            raise ModelRouterError(
                f"HTTP {status_code} from {endpoint.provider}/{endpoint.model}: {response_text[:200]}"
            )

        data = json.loads(response_text)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelRouterError(
                f"Malformed completion payload from {endpoint.provider}/{endpoint.model}: {data}"
            ) from exc

    async def complete_json(
        self,
        task_type: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: Optional[Type[T]] = None,
        pii_redactor: Optional[PIIRedactor] = None,
        reinject_pii_in_output: bool = False,
        temperature: float = 0.1,
    ) -> Union[Dict[str, Any], T]:
        """
        Routes a prompt through the configured provider chain for `task_type`.
        1. Redacts PII from `system_prompt` and `user_prompt` if `pii_redactor` is provided.
        2. Attempts each endpoint in the route chain; retries `RateLimitError` (429)
           with exponential backoff before failing over to the next provider.
        3. Enforces valid JSON output and validates against `response_schema` if passed.
        4. Optionally re-injects PII placeholders in the parsed JSON response.
        """
        if task_type not in TASK_ROUTES:
            raise ValueError(
                f"Unknown task_type '{task_type}'. Valid tasks: {list(TASK_ROUTES.keys())}"
            )

        safe_system_prompt = (
            pii_redactor.redact_text(system_prompt) if pii_redactor else system_prompt
        )
        safe_user_prompt = (
            pii_redactor.redact_text(user_prompt) if pii_redactor else user_prompt
        )

        # Ensure JSON instruction is explicitly present in system prompt (required by OpenAI/Groq JSON mode)
        if "json" not in safe_system_prompt.lower() and "json" not in safe_user_prompt.lower():
            safe_system_prompt = f"{safe_system_prompt.strip()}\nRespond strictly in valid JSON format."

        messages = [
            {"role": "system", "content": safe_system_prompt},
            {"role": "user", "content": safe_user_prompt},
        ]

        endpoints = TASK_ROUTES[task_type]
        errors: List[str] = []

        for endpoint in endpoints:
            for attempt in range(1, self.max_retries_per_endpoint + 1):
                try:
                    raw_content = await self._call_endpoint_openai_compatible(
                        endpoint=endpoint,
                        messages=messages,
                        temperature=temperature,
                    )
                    parsed_dict = extract_clean_json(raw_content)

                    if pii_redactor and reinject_pii_in_output:
                        parsed_dict = pii_redactor.reinject_data(parsed_dict)

                    if response_schema is not None:
                        return response_schema.model_validate(parsed_dict)

                    return parsed_dict

                except RateLimitError as rl_err:
                    wait_time = self.base_backoff_seconds * (2 ** (attempt - 1))
                    logger.warning(
                        "[ModelRouter] 429 Rate Limit on %s/%s (attempt %d/%d). Backing off %.2fs...",
                        endpoint.provider,
                        endpoint.model,
                        attempt,
                        self.max_retries_per_endpoint,
                        wait_time,
                    )
                    errors.append(f"{endpoint.provider}/{endpoint.model} (429 attempt {attempt}): {rl_err}")
                    if attempt < self.max_retries_per_endpoint:
                        await asyncio.sleep(wait_time)
                    else:
                        logger.warning(
                            "[ModelRouter] Exhausted retries for %s/%s, failing over to next provider.",
                            endpoint.provider,
                            endpoint.model,
                        )

                except (json.JSONDecodeError, ValidationError, ValueError) as val_err:
                    logger.warning(
                        "[ModelRouter] Schema/JSON validation error on %s/%s (attempt %d/%d): %s",
                        endpoint.provider,
                        endpoint.model,
                        attempt,
                        self.max_retries_per_endpoint,
                        val_err,
                    )
                    errors.append(f"{endpoint.provider}/{endpoint.model} (validation): {val_err}")
                    break  # Fail over to next endpoint on malformed output

                except Exception as exc:
                    logger.warning(
                        "[ModelRouter] Error on %s/%s: %s. Failing over...",
                        endpoint.provider,
                        endpoint.model,
                        exc,
                    )
                    errors.append(f"{endpoint.provider}/{endpoint.model}: {exc}")
                    break

        raise ModelRouterError(
            f"All providers failed for task '{task_type}'. Errors: {' | '.join(errors)}"
        )

    # ==========================================================================
    # 5. 384-DIM SEMANTIC EMBEDDINGS (`all-MiniLM-L6-v2` / Deterministic Fallback)
    # ==========================================================================

    @property
    def embedding_version(self) -> str:
        if settings.EMBEDDING_PROVIDER == 'hash':
            return 'sha256-unigram-bigram-384-v1'
        return f'sentence-transformers:{settings.EMBEDDING_MODEL_NAME}:384-v1'

    def embed_text(self, text: str) -> List[float]:
        if settings.EMBEDDING_PROVIDER == 'hash':
            return self.deterministic_hash_embedding(text, dim=384)
        if settings.EMBEDDING_PROVIDER != 'local':
            raise ModelRouterError('EMBEDDING_PROVIDER must be local or hash.')
        if self._local_embedder is None:
            from sentence_transformers import SentenceTransformer
            self._local_embedder = SentenceTransformer(settings.EMBEDDING_MODEL_NAME)
        vec = self._local_embedder.encode(text, normalize_embeddings=True)
        if len(vec) != 384:
            raise ModelRouterError('Embedding model must produce 384 dimensions.')
        return [float(x) for x in vec.tolist()]

    @staticmethod
    def deterministic_hash_embedding(text: str, dim: int = 384) -> List[float]:
        """
        Computes a normalized 384-d feature-hashed vector from word unigrams and bigrams.
        Preserves cosine similarity between texts sharing technical skills and role keywords
        even when running in air-gapped or test environments without GPU/model weights.
        """
        tokens = re.findall(r"[a-z0-9+#.]+", text.lower())
        if not tokens:
            return [0.0] * dim

        vec = [0.0] * dim
        # Weight unigrams higher (2.0) and bigrams lower (0.5) so shared technical
        # skills and role terms produce strong cosine similarity (> 0.55).
        for tok in tokens:
            digest = hashlib.sha256(tok.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "little") % dim
            sign = 1.0 if (digest[4] % 2 == 0) else -1.0
            vec[idx] += 2.0 * sign

        for i in range(len(tokens) - 1):
            bigram = f"{tokens[i]}_{tokens[i + 1]}"
            digest = hashlib.sha256(bigram.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "little") % dim
            sign = 1.0 if (digest[4] % 2 == 0) else -1.0
            vec[idx] += 0.5 * sign

        return ModelRouter._normalize_and_fit_dim(vec, target_dim=dim)

    @staticmethod
    def _normalize_and_fit_dim(vec: List[float], target_dim: int = 384) -> List[float]:
        if len(vec) > target_dim:
            vec = vec[:target_dim]
        elif len(vec) < target_dim:
            vec = vec + [0.0] * (target_dim - len(vec))

        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 1e-12:
            vec = [round(x / norm, 6) for x in vec]
        return vec

    @staticmethod
    def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        if len(vec_a) != len(vec_b) or not vec_a:
            return 0.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))
        if norm_a < 1e-12 or norm_b < 1e-12:
            return 0.0
        return dot / (norm_a * norm_b)
