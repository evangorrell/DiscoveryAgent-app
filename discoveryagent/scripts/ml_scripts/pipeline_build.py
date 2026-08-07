import argparse
import asyncio
from pathlib import Path
import sys
import re
import os

# Configure Claude Code CLI path BEFORE importing SDK
project_root = Path(__file__).parent.parent.parent.absolute()  # Go up to discoveryagent root
claude_cli_path = project_root / "node_modules/@anthropic-ai/claude-code/cli.js"

# Set environment variables
os.environ['CLAUDE_CODE_CLI_PATH'] = str(claude_cli_path)
os.environ['NODE_PATH'] = str(project_root / "node_modules")
os.environ["MAX_MCP_OUTPUT_TOKENS"] = "50000"

# Verify the CLI path exists
if not claude_cli_path.exists():
    print(f"Warning: Claude Code CLI not found at {claude_cli_path}")
    print(f"Please ensure Claude Code is installed with: npm install @anthropic-ai/claude-code")

try:
    from claude_agent_sdk import query, ClaudeAgentOptions
except ImportError:
    print("claude-code-sdk not installed. Please install with: pip install claude-code-sdk", file=sys.stderr)
    sys.exit(1)

from scripts.ml_scripts.prompt import ML_PIPELINE_PROMPT


def build_system_prompt(output_directory: Path, data_directory: Path) -> str:
    return f"""
You are Claude Code, an autonomous coding and analysis system with full repo/file access.

Your environment:
- Working directory: {output_directory}
- Dataset information available at: {data_directory} (you can read files from this directory using absolute paths)
- You may use tools: Read, Write, Bash
- You can create files and directories, and write Python/Markdown/text outputs

Critical file safety rules:
- Create all files within the provided output directory: {output_directory}
- You can read dataset files from: {data_directory}
- Use descriptive filenames to avoid conflicts
- Always save generated artifacts within the appropriate subfolders

Dataset access:
- The dataset description has already been loaded and provided to you in the user prompt
- You can access data files using their absolute paths starting with {data_directory}
- Example: Use Read tool with absolute path like "{data_directory}/some_data_file.csv"

Deliverable requirements:
- At the end of your work, append a section titled "## Generated Files" that lists ALL created files with absolute paths, one per line prefixed by "- ".
- Ensure all code is well-formatted and follows Python best practices
- Create comprehensive unit tests and clear documentation

IMPORTANT: Focus on creating a robust ML pipeline that can handle various types of machine learning models and frameworks.
"""


def parse_stream_message(message) -> str:
    """Best-effort formatter for Claude Code streamed messages for readable stdout."""
    msg_str = str(message)
    try:
        if "SystemMessage" in msg_str and "subtype='init'" in msg_str:
            return "Initializing Claude Code session..."
        if "AssistantMessage" in msg_str and "TextBlock" in msg_str:
            m = re.search(r'TextBlock\(text="([^"]*)"', msg_str) or re.search(r"TextBlock\(text='([^']*)'", msg_str)
            if m:
                text = m.group(1).replace('\\n', '\n').replace('\\"', '"').replace("\\'", "'")
                return text[:400] + ("..." if len(text) > 400 else "")
            return "[Assistant]"
        if "AssistantMessage" in msg_str and "ToolUseBlock" in msg_str:
            tool = re.search(r"name='([^']*)'", msg_str) or re.search(r'name="([^"]*)"', msg_str)
            tool_name = tool.group(1) if tool else "tool"
            desc = re.search(r"'description': '([^']*)'", msg_str) or re.search(r'"description": "([^"]*)"', msg_str)
            return f"Using {tool_name}: {desc.group(1) if desc else ''}"
        if "UserMessage" in msg_str and "ToolResultBlock" in msg_str:
            content = re.search(r"content='([^']*)'", msg_str) or re.search(r'content="([^"]*)"', msg_str)
            if content:
                c = content.group(1).replace('\\n', ' ')
                return f"Tool result: {c[:300]}{'...' if len(c)>300 else ''}"
            return "[Tool result]"
    except Exception:
        pass
    return "[Message update]"


async def run_pipeline_build(dataset_description: str, output_directory: Path, data_directory: Path, max_turns: int, model: str = None) -> str:
    # Check if ANTHROPIC_API_KEY is set
    if not os.getenv('ANTHROPIC_API_KEY'):
        print("Warning: ANTHROPIC_API_KEY environment variable is not set.")
        print("Please set it with: export ANTHROPIC_API_KEY='your_api_key_here'")
        print("Or ensure it's available in your environment.")
    
    system_prompt = build_system_prompt(output_directory, data_directory)
    options = ClaudeAgentOptions(
        max_turns=max_turns,
        max_buffer_size=int(os.getenv("CLAUDE_CODE_MAX_BUFFER_SIZE", str(50 * 1024 * 1024))),
        system_prompt=system_prompt,
        cwd=output_directory,
        allowed_tools=["Read", "Write", "Bash"],
        permission_mode="acceptEdits",
        model=model
    )

    user_prompt = ML_PIPELINE_PROMPT.format(
        dataset_description=dataset_description,
        output_directory=str(output_directory)
    )

    print(f"Running Claude Code in: {output_directory}")
    print(f"Max turns: {max_turns}")
    print("--- Streaming progress ---")

    messages = []
    async for msg in query(prompt=user_prompt, options=options):
        messages.append(str(msg))
        pretty = parse_stream_message(msg)
        if pretty:
            print(pretty)

    print("--- Run complete ---\n")
    return "\n".join(messages)


def extract_generated_files(raw_output: str):
    patterns = [
        r"## Generated Files\s*\n((?:- [^\n]+\n?)*)",
        r"Generated Files:\s*\n((?:- [^\n]+\n?)*)",
        r"Files Created:\s*\n((?:- [^\n]+\n?)*)",
        r"Output Files:\s*\n((?:- [^\n]+\n?)*)",
    ]
    for pat in patterns:
        m = re.search(pat, raw_output, re.IGNORECASE | re.MULTILINE)
        if m:
            block = m.group(1)
            files = []
            for line in block.splitlines():
                line = line.strip()
                if line.startswith("- "):
                    fp = line[2:].strip()
                    if fp:
                        files.append(fp)
            if files:
                return files
    return []


def load_dataset_description(working_directory):
    """Load dataset description from README.txt in the data directory"""
    data_description_file = os.path.join(working_directory, "data", "README.txt")
    if os.path.exists(data_description_file):
        try:
            with open(data_description_file, 'r') as f:
                dataset_description = f.read()
                # Replace datapath placeholder with actual path
                dataset_description = dataset_description.replace("{datapath}", os.path.join(working_directory, "data"))
                return dataset_description
        except Exception as e:
            print(f"Error: Could not read dataset description from {data_description_file}: {e}")
            sys.exit(1)
    else:
        print(f"Error: Dataset description file not found: {data_description_file}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Build ML pipeline and unit tests using Claude Code")
    parser.add_argument("--working_directory", type=str, default=None,
                       help="Working directory path where the dataset is located")
    parser.add_argument("--dataset_name", type=str, default=None,
                       help="Dataset name (will be appended with '_data' if needed)")
    parser.add_argument("--output_directory", help="Path where pipeline files should be created (default: dataset_dir/inference_tests)", required=False)
    parser.add_argument("--max_turns", type=int, default=15, help="Maximum interaction turns")
    parser.add_argument("--model", help="Claude model to use", default="claude-sonnet-4-5-20250929")
    args = parser.parse_args()

    # Determine working directory using the same logic as run_air_sherlock.py
    SHERLOCK_DATA_DIR = "/scratch/users/oqueen/AIR_project/active"
    
    if args.dataset_name is not None:
        if not args.dataset_name.endswith("_data"):
            args.dataset_name = f"{args.dataset_name}_data"
        args.working_directory = os.path.join(SHERLOCK_DATA_DIR, args.dataset_name)
        if not os.path.exists(args.working_directory):
            print(f"Error: Dataset directory does not exist: {args.working_directory}", file=sys.stderr)
            sys.exit(2)
    elif args.working_directory is not None:
        if not os.path.exists(args.working_directory):
            print(f"Error: Working directory does not exist: {args.working_directory}", file=sys.stderr)
            sys.exit(2)
    else:
        print("Error: Either --dataset_name or --working_directory must be provided", file=sys.stderr)
        sys.exit(2)

    print(f"Configuration:")
    print(f"  Dataset name: {args.dataset_name}")
    print(f"  Working directory: {args.working_directory}")
    print(f"  Output directory: {args.output_directory}")
    print(f"  Max turns: {args.max_turns}")
    print(f"  Claude model: {args.model}")
    print()

    # Set default output directory if not provided
    if args.output_directory is None:
        args.output_directory = os.path.join(args.working_directory, "inference_tests")

    # Load dataset description from data/README.txt
    dataset_description = load_dataset_description(args.working_directory)

    # Create output directory
    output_dir = Path(args.output_directory).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get data directory path for permissions
    data_dir = Path(os.path.join(args.working_directory, "data"))

    try:
        raw = asyncio.run(run_pipeline_build(dataset_description, output_dir, data_dir, args.max_turns, args.model))
    except KeyboardInterrupt:
        print("Interrupted by user.")
        sys.exit(130)

    files = extract_generated_files(raw)
    if files:
        print("Generated files:")
        for f in files:
            print(f"- {f}")
    else:
        print("No generated files block detected. Review the console output above for details.")


if __name__ == "__main__":
    main()