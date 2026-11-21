# ============================================================================
# Sentinel — Vibration Analysis & Measurement Validation Engine
# ============================================================================

.PHONY: all setup test clean verify-hashes bootstrap-portfolio verify-portfolio verify-schema help

# Default to prepared local virtual environment python if present, otherwise python3
VENV_PYTHON := $(CURDIR)/.release/venv/bin/python
ifeq ($(origin PYTHON), undefined)
  ifneq ($(wildcard $(VENV_PYTHON)),)
    PYTHON := $(VENV_PYTHON)
  else
    PYTHON := python3
  endif
endif

export PYTHONPATH := $(CURDIR)/adi-common/src:$(CURDIR)/adi-runtime/src:$(CURDIR)/adi-app/src:$(PYTHONPATH)

help:
	@echo "Available targets:"
	@echo "  setup              - Set up development environment and local virtual environment"
	@echo "  test               - Run all test suites across components (adi-common, adi-runtime, adi-app)"
	@echo "  verify-hashes      - Verify historical evidence digests"
	@echo "  bootstrap-portfolio- Build wheels and set up local offline environment (.release/venv)"
	@echo "  verify-portfolio   - Test isolated offline relocated wheel installation and entry points"
	@echo "  verify-schema      - Verify Stage 11 Draft 2020-12 schema & semantic admission invariants"
	@echo "  clean              - Remove build artifacts and temporary files"

setup:
	@echo "Setting up workspace..."
	@sh scripts/bootstrap_portfolio.sh

test:
	@echo "Running tests with $(PYTHON)..."
	@$(PYTHON) -c "import jsonschema" 2>/dev/null || { \
		echo "ERROR: Missing required dependency 'jsonschema' in $(PYTHON)." >&2; \
		echo "Action: Run 'make setup' (or 'sh scripts/bootstrap_portfolio.sh') to build the local virtual environment with required dependencies, or invoke with PYTHON=.release/venv/bin/python." >&2; \
		exit 1; \
	}
	@$(PYTHON) -m unittest discover -s adi-common/tests
	@$(PYTHON) -m unittest discover -s adi-runtime/tests
	@$(PYTHON) -m unittest discover -s adi-app/tests

verify-hashes:
	@$(PYTHON) scripts/verify_historical_hashes.py

bootstrap-portfolio:
	@sh scripts/bootstrap_portfolio.sh

verify-portfolio:
	@$(PYTHON) scripts/verify_portfolio_release.py

verify-schema:
	@$(PYTHON) scripts/verify_stage11_schema.py

clean:
	@rm -rf build dist .pytest_cache .release/wheels .release/venv
	@find . -type d -name "__pycache__" -exec rm -rf {} +
	@find . -type f -name "*.pyc" -delete
