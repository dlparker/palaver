#!/bin/bash
# Run tests sequentially to avoid whispercpp core dumps
# Coverage accumulates across runs (uses --cov-append from pytest.ini)

set -e  # Exit on error

# Color output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Running tests sequentially...${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Array of test files to run
TEST_FILES=(
    "tests/test_draft_builder.py"
    "tests/test_file_audio_to_text.py"
    "tests/test_mic_mock_to_text.py"
    "tests/test_file_sender_example.py"
    "tests/test_direct_server.py"
    "tests/test_websocket_servers.py::test_direct_to_remote_websocket"
    "tests/test_websocket_servers.py::test_direct_to_rescan_websocket"
)

# Track failures
FAILED_TESTS=()

# Run each test file
for test in "${TEST_FILES[@]}"; do
    echo -e "${BLUE}Running: ${test}${NC}"
    echo "----------------------------------------"

    if uv run pytest "$test" -v; then
        echo -e "${GREEN}✓ PASSED${NC}"
    else
        echo -e "${RED}✗ FAILED${NC}"
        FAILED_TESTS+=("$test")
    fi

    echo ""
done

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Test Summary${NC}"
echo -e "${BLUE}========================================${NC}"

if [ ${#FAILED_TESTS[@]} -eq 0 ]; then
    echo -e "${GREEN}All tests passed!${NC}"
    echo ""
    echo "Coverage report: htmlcov/index.html"
    exit 0
else
    echo -e "${RED}Failed tests:${NC}"
    for test in "${FAILED_TESTS[@]}"; do
        echo -e "  ${RED}✗${NC} $test"
    done
    exit 1
fi
