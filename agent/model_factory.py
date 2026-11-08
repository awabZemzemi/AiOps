"""Create prompted chat models from independent, named profiles."""

import os

from langchain.chat_models import init_chat_model
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import Runnable

from .prompts.gpt_oss_120b import SYSTEM_PROMPT


class LLMFactory:
    """Static factory with a separate provider, configuration, and prompt per profile.

    Add a registry entry with its own model identifier and system prompt to
    support another profile, including another model from an existing provider.
    """

    PROFILES = {
        "gpt_oss_120b": {
            "provider": "groq",
            "model": "openai/gpt-oss-120b",
            "api_key_env": "GROQ_API_KEY",
            "system_prompt": SYSTEM_PROMPT,
        },
    }

    @staticmethod
    def create(profile: str, tools=None) -> Runnable:
        """Return a prompt/model pipeline using credentials from the environment.

        Raises ValueError for an unknown profile or an unset/blank API key.
        Only the selected profile's configuration is required.
        """
        if profile not in LLMFactory.PROFILES:
            available = ", ".join(sorted(LLMFactory.PROFILES))
            raise ValueError(
                f"Unknown LLM profile {profile!r}. Available profiles: {available}."
            )

        settings = LLMFactory.PROFILES[profile]
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
            [
                ("system", settings["system_prompt"]),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )
        return prompt | llm
