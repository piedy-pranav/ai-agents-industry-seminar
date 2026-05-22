import os
import re
from langchain_core.messages import SystemMessage, HumanMessage

# Qwen3 models can emit <think>...</think> reasoning blocks before the answer,
# and sometimes echo word-count compliance notes like "(119 words)".
# Strip both so agents only see clean response text.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_WORD_COUNT_RE = re.compile(r"\s*\(\d+\s+words?\)\s*$", re.IGNORECASE)


def _strip_thinking(text: str) -> str:
    text = _THINK_RE.sub("", text).strip()
    text = _WORD_COUNT_RE.sub("", text).strip()
    return text


def call_llm(system: str, user: str, temperature: float = 0.2) -> str:
    """
    Invoke an LLM with a system + user message pair.
    Primary: Groq qwen/qwen3-32b (free tier).
    Fallback: Claude claude-haiku-4-5-20251001 on rate-limit or any Groq failure.
    Returns an empty string if neither key is configured — the pipeline
    continues without crashing.
    """
    messages = [SystemMessage(content=system), HumanMessage(content=user)]

    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if groq_key:
        try:
            from langchain_groq import ChatGroq
            llm = ChatGroq(
                model="qwen/qwen3-32b",
                temperature=temperature,
                api_key=groq_key,
            )
            raw = llm.invoke(messages).content
            return _strip_thinking(raw)
        except Exception as e:
            err = str(e).lower()
            if "429" in err or "rate" in err:
                print("[LLM] Groq rate limit hit — falling back to Claude.")
            else:
                print(f"[LLM] Groq error: {e} — falling back to Claude.")

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key:
        try:
            from langchain_anthropic import ChatAnthropic
            llm = ChatAnthropic(
                model="claude-haiku-4-5-20251001",
                temperature=temperature,
                api_key=anthropic_key,
            )
            return llm.invoke(messages).content
        except Exception as e:
            print(f"[LLM] Claude error: {e}")

    return ""  # graceful no-op when no keys are set
