import json
from collections.abc import Callable

from functions.get_files_info import get_files_info, schema_get_files_info
from functions.get_file_content import get_file_content, schema_get_file_content
from functions.run_python_file import run_python_file, schema_run_python_file
from functions.write_file import write_file, schema_write_file


available_functions = [
    schema_get_files_info,
    schema_get_file_content,
    schema_run_python_file,
    schema_write_file,
]


def call_function(function_name: str, arguments: str, call_id: str) -> dict:
    """Execute one tool call, returning a tool message.

    Invalid arguments and tool failures are returned to the model as tool
    output so it can recover instead of aborting the turn.
    """

    try:
        function_args = json.loads(arguments or "{}")
    except json.JSONDecodeError as exc:
        return _error(call_id, f"invalid JSON in tool arguments: {exc}")

    if not isinstance(function_args, dict):
        return _error(call_id, "tool arguments must be a JSON object")

    function_map: dict[str, Callable[..., str]] = {
        "get_files_info": get_files_info,
        "get_file_content": get_file_content,
        "run_python_file": run_python_file,
        "write_file": write_file,
    }

    if function_name not in function_map:
        return _error(call_id, f"Unknown function: {function_name}")

    function_args["working_directory"] = "./calculator"

    try:
        result = function_map[function_name](**function_args)
    except Exception as exc:
        return _error(call_id, f"{function_name} failed: {exc}")

    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": result,
    }


def _error(call_id: str, message: str) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": f"Error: {message}",
    }
