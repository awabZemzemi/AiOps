"""Create prompted chat models from independent, named profiles."""

import os

from langchain.chat_models import init_chat_model
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import Runnable

from ..prompts import SYSTEM_PROMPT


PROFILES = {
    "gpt_oss_120b": {
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "api_key_env": "GROQ_API_KEY",
        "system_prompt": SYSTEM_PROMPT,
    },
}


def create_model(profile: str = "gpt_oss_120b", tools=None) -> Runnable:
    """Build the selected prompt/model pipeline, optionally binding tools.

    Raises ValueError for an unknown profile or an unset/blank API key.
    """
    if profile not in PROFILES:
        available = ", ".join(sorted(PROFILES))
        raise ValueError(
            f"Unknown LLM profile {profile!r}. Available profiles: {available}."
        )

    settings = PROFILES[profile]
    api_key = os.environ.get(settings["api_key_env"], "").strip()
    if not api_key:
        raise ValueError(
            f"Missing configuration for LLM profile {profile!r}: "
            f"set the {settings['api_key_env']} environment variable."
        )

    llm = init_chat_model(
        model=settings["model"],
        model_provider=settings["provider"],
        api_key=api_key,
    )
    if tools:
        llm = llm.bind_tools(tools)

    prompt = ChatPromptTemplate.from_messages(
        [("system", settings["system_prompt"]), MessagesPlaceholder("messages")]
    )
    return prompt | llm
