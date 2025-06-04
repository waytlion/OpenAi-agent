import openai
client = openai.OpenAI(
    api_key="sk-6uV8zFo9OcPqgMD5R4Bb3g",
    base_url="http://188.245.32.59:4000" # LiteLLM Proxy is OpenAI compatible, Read More: https://docs.litellm.ai/docs/proxy/user_keys
)

response = client.chat.completions.create(
    model="gpt-4o", # model to send to the proxy
    messages = [
        {
            "role": "user",
            "content": "this is a test request, write a short poem"
        }
    ]
)

print(response)

