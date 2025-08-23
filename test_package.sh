ARCH=$(uname -m) \
TARGET_DIR="modules/runner_registration/lambda" \
MODULE_DIR="modules/runner_registration" \
REQUIREMENTS_FILE="modules/runner_registration/lambda/requirements.txt" \
bash modules/runner_registration/package.sh
