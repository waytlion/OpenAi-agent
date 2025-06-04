import asyncio
import os

from openai import AsyncOpenAI

from agents import (
    Agent,
    Runner,
    function_tool,
    set_default_openai_api,
    set_default_openai_client,
    set_tracing_disabled,
)

#! Das hier ist Ollama
BASE_URL = os.getenv("EXAMPLE_BASE_URL") or "http://localhost:11434/v1"
API_KEY = os.getenv("EXAMPLE_API_KEY") or "ollama"
MODEL_NAME = os.getenv("EXAMPLE_MODEL_NAME") or "gemma3:1b"

#! Das hier ist LittleLLM
#BASE_URL = os.getenv("EXAMPLE_BASE_URL") or "http://188.245.32.59:4000"
#API_KEY = os.getenv("EXAMPLE_API_KEY") or "sk-6uV8zFo9OcPqgMD5R4Bb3g"
#MODEL_NAME = os.getenv("EXAMPLE_MODEL_NAME") or "gpt-4o-mini"

if not BASE_URL or not API_KEY or not MODEL_NAME:
    raise ValueError(
        "Please set EXAMPLE_BASE_URL, EXAMPLE_API_KEY, EXAMPLE_MODEL_NAME via env var or code."
    )


"""This example uses a custom provider for all requests by default. We do three things:
1. Create a custom client.
2. Set it as the default OpenAI client, and don't use it for tracing.
3. Set the default API as Chat Completions, as most LLM providers don't yet support Responses API.

Note that in this example, we disable tracing under the assumption that you don't have an API key
from platform.openai.com. If you do have one, you can either set the `OPENAI_API_KEY` env var
or call set_tracing_export_api_key() to set a tracing specific key.
"""

client = AsyncOpenAI(
    base_url=BASE_URL,
    api_key=API_KEY,
)
set_default_openai_client(client=client, use_for_tracing=False)
set_default_openai_api("chat_completions")
set_tracing_disabled(disabled=True)


@function_tool
def get_weather(city: str):
    print(f"[debug] getting weather for {city}")
    return f"The weather in {city} is sunny."
@function_tool
def echo(msg: str):
    print(f"[debug] echo tool called with: {msg}")
    return f"Echo: {msg}"



async def main():
    agent = Agent(
        name="Assistant",
        instructions="You only respond in haikus.",
        model=MODEL_NAME,
    )

    result = await Runner.run(agent, "What's the weather in Tokyo?", max_turns=1)
    print(result.final_output)
    # Then run:
    result = await Runner.run(agent, "Use the echo tool to repeat 'hello world'", max_turns=2)

if __name__ == "__main__":
    asyncio.run(main())
