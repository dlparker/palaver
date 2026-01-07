#!/bin/bash
# Clear coverage data to start fresh

echo "Clearing coverage data..."

# Remove .coverage file (contains coverage data)
if [ -f .coverage ]; then
    rm .coverage
    echo "✓ Removed .coverage"
else
    echo "  .coverage not found (already clean)"
fi

# Remove coverage HTML report directory
if [ -d htmlcov ]; then
    rm -rf htmlcov
    echo "✓ Removed htmlcov/"
else
    echo "  htmlcov/ not found (already clean)"
fi

echo ""
echo "Coverage data cleared. Run tests to generate fresh coverage."
