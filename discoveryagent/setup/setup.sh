# AIR Setup Script
# This script sets up the AIR environment using uv

set -e

echo "🚀 Setting up AIR environment..."
# Default virtualenv directory (can be overridden with -v|--venv)
VENV_DIR=".venv"

# Parse options
while [[ $# -gt 0 ]]; do
    case "$1" in
        -v|--venv)
            VENV_DIR="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [-v|--venv VENV_DIR]"
            exit 0
            ;;
        --) # end option parsing
            shift
            break
            ;;
        -*)
            echo "Unknown option: $1"
            exit 1
            ;;
        *)
            shift
            ;;
    esac
done
# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "❌ uv is not installed."
    echo "Please install uv first: https://docs.astral.sh/uv/getting-started/installation/"
    echo ""
    echo "Quick install command:"
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo ""
    echo "After installing, restart your terminal or run: source ~/.bashrc"
    exit 1
fi

echo "✅ uv is already installed"

# Check if we're in the AIR directory
if [ ! -f "pyproject.toml" ]; then
    echo "❌ Please run this script from the AIR directory"
    exit 1
fi

# Create virtual environment and install dependencies
echo "📦 Creating virtual environment and installing dependencies..."
uv sync

# Install AIR package in development mode
echo "📦 Installing AIR package in development mode..."
uv pip install -e .

# Ensure pip is available and up to date in the virtual environment
echo "🔧 Setting up pip in virtual environment..."

# If a virtualenv is already active, use it. Otherwise try to activate the
# provided venv directory (or the default .venv).
if [ -n "${VIRTUAL_ENV:-}" ]; then
    echo "🔧 Virtual environment already active at $VIRTUAL_ENV"
else
    if [ -f "${VENV_DIR}/bin/activate" ]; then
        echo "🔧 Activating virtual environment at $VENV_DIR"
        # shellcheck source=/dev/null
        source "${VENV_DIR}/bin/activate"
    else
        echo "❌ Virtual environment not found at ${VENV_DIR}"
        exit 1
    fi
fi

# Install pip using uv's ensure-pip functionality
echo "🔧 Installing pip..."
python3 -m ensurepip --upgrade

# Install uv in the virtual environment for convenience
echo "🔧 Installing uv in virtual environment..."
python3 -m pip install uv

echo "✅ Setup complete!"
echo ""
echo "🔧 Virtual environment status:"
if [ -n "${VIRTUAL_ENV:-}" ]; then
    echo "✅ Using active virtual environment at $VIRTUAL_ENV"
else
    echo "✅ Virtual environment activated at ${VENV_DIR}"
fi
echo ""
echo "📦 Package managers available in this environment:"
echo "  - pip (updated to latest version)"
echo "  - uv (installed in virtual environment)"
echo ""