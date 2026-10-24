import os
import argparse
from dotenv import load_dotenv
from openai import OpenAI
from prompts import system_prompt
import json
from call_function import available_functions


load_dotenv()
api_key = os.environ.get("OPENROUTER_API_KEY")

if api_key is None:
    raise RuntimeError("Openrouter api key not found in env")

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key= api_key,
)

parser = argparse.ArgumentParser(description="Agent47")



def main():
    parser.add_argument("user_prompt", type=str, help="User prompt")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")
    args = parser.parse_args()
    
    messages = [
    {
        "role": "system",
        "content": system_prompt
    },
    {
        "role": "user",
        "content": args.user_prompt
    }
    ]
    
    response = client.chat.completions.create(
    model="openrouter/free",
    messages=messages,
    # temperature=0
    tools=available_functions,
    )   
    if response.usage is None:
        raise RuntimeError("Failed to retrieve token usage")

    if args.verbose:
        print(f"User prompt: {args.user_prompt}\nPrompt tokens: {response.usage.prompt_tokens}\nResponse tokens: {response.usage.completion_tokens}")

    message = response.choices[0].message
    if message.tool_calls:
        for tool_call in message.tool_calls:
            function_args = json.loads(tool_call.function.arguments or "{}")
        print(f"Calling function: {tool_call.function.name}({function_args})")
    else:
        print(message.content)

if __name__ == "__main__":
    main()
