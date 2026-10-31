#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3.11}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "python3.11 not found, falling back to python3"
    PYTHON_BIN="python3"
fi

echo "Using $($PYTHON_BIN --version)"

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    "$PYTHON_BIN" -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate

echo "Installing dependencies..."
pip install --upgrade pip >/dev/null
pip install -r requirements.txt

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "Created .env from .env.example — fill in your API keys and Google Sheets config."
else
    echo ".env already exists, leaving it as-is."
fi

echo ""
echo "Setup complete. Next steps:"
echo "  1. source venv/bin/activate"
echo "  2. Edit .env, and put your Google service account JSON at the path it points to"
echo "  3. Run: python bot.py"
