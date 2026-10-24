import os
from config import MAX_CHARS


def get_file_content(working_directory: str, file_path: str) -> str:
    try:
        working_dir_abs = os.path.abspath(working_directory)
        target_file = os.path.normpath(os.path.join(working_dir_abs,file_path))

        valid_target_file = (os.path.commonpath([working_dir_abs,target_file])== working_dir_abs)

        if not valid_target_file:
            return(
                f'Error: Cannot read "{file_path}" as it is outside the permitted working directory'
            )
        if not os.path.isfile(target_file):
            return (
                f'Error: File not found or is not a regular file: "{file_path}"'
            )
        with open(target_file,"r", encoding="utf-8") as f:
            content = f.read(MAX_CHARS)

            if f.read(1):
                content += (f'[...File "{file_path}" truncated at {MAX_CHARS} characters]')

        return content
    except Exception as e: 
        return f"Error: {e}"



schema_get_file_content = {
    "type": "function",
    "function": {
        "name": "get_file_content",
        "description": "Reads the contents of a file.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path of the file to read, relative to the working directory.",
                },
            },
            "required": ["file_path"],
        },
    },
}
