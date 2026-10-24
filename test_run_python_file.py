
from functions.run_python_file import run_python_file


def main():
    print('Result for main.py:')
    print(run_python_file("calculator", "main.py"))

    print('\nResult for main.py with arguments:')
    print(run_python_file("calculator", "main.py", ["3 + 5"]))

    print('\nResult for tests.py:')
    print(run_python_file("calculator", "tests.py"))

    print('\nResult for ../main.py:')
    print(run_python_file("calculator", "../main.py"))

    print('\nResult for nonexistent.py:')
    print(run_python_file("calculator", "nonexistent.py"))

    print('\nResult for lorem.txt:')
    print(run_python_file("calculator", "lorem.txt"))


if __name__ == "__main__":
    main()

