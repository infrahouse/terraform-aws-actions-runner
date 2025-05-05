set -eux

mkdir -p "$TARGET_DIR"
pip install --target "$TARGET_DIR" -r "$REQUIREMENTS_FILE" --upgrade
find "$TARGET_DIR" -name __pycache__ -exec rm -rv {} +
