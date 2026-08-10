import json
import argparse
from pathlib import Path

def print_json_structure(obj, indent=0, max_depth=3):
    """Recursively prints the structure of a JSON object."""
    prefix = "  " * indent
    if isinstance(obj, dict):
        print(f"{prefix}{{dict}} keys:")
        if indent < max_depth:
            for key, value in obj.items():
                print(f"{prefix}  - {key}:")
                print_json_structure(value, indent + 2, max_depth)
        else:
            print(f"{prefix}  ...")
    elif isinstance(obj, list):
        print(f"{prefix}[list] length={len(obj)}")
        if len(obj) > 0 and indent < max_depth:
            print(f"{prefix}  [0]:")
            print_json_structure(obj[0], indent + 2, max_depth)
        elif len(obj) == 0:
            print(f"{prefix}  (empty list)")
        else:
            print(f"{prefix}  ...")
    else:
        print(f"{prefix}{type(obj).__name__}")

def main(filepath):
    path = Path(filepath)
    with open(path, 'r') as f:
        data = json.load(f)
    print(f"Structure of {path.name}:")
    print_json_structure(data)

if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description='Print the structure of a JSON file (recursively up to a certain depth).'
    )
    parser.add_argument('json_file', type=str, help='Path to the JSON file')
    parser.add_argument('--max_depth', type=int, default=3, help='Maximum recursion depth (default: 3)')
    args = parser.parse_args()
    main(args.json_file)
