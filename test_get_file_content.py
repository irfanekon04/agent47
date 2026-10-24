
from functions.get_file_content import get_file_content


def main():
    result = get_file_content("calculator", "lorem.txt")
    print(f"lorem.txt length: {len(result)}")
    print(f"lorem.txt truncated: {'truncated' in result}")

    print("\nResult for main.py:")
    print(get_file_content("calculator", "main.py"))

    print("\nResult for pkg/calculator.py:")
    print(get_file_content("calculator", "pkg/calculator.py"))

    print("\nResult for /bin/cat:")
    print(get_file_content("calculator", "/bin/cat"))

    print("\nResult for missing file:")
    print(
        get_file_content(
            "calculator", "pkg/does_not_exist.py"
        )
    )


if __name__ == "__main__":
    main()
