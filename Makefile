.PHONY: help install dev test eval smoke-gemini smoke-openai deploy clean

# Use the project-local venv if present; fall back to system python.
VENV       := .venv
PYTHON     := $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)
PIP        := $(if $(wildcard $(VENV)/bin/pip),$(VENV)/bin/pip,pip3)
UVICORN    := $(if $(wildcard $(VENV)/bin/uvicorn),$(VENV)/bin/uvicorn,uvicorn)
PYTEST     := $(if $(wildcard $(VENV)/bin/pytest),$(VENV)/bin/pytest,pytest)

help:
	@echo "Targets:"
	@echo "  install        Create .venv (if missing) and install requirements.txt"
	@echo "  dev            Run the FastAPI app with hot reload on $$HOST:$$PORT"
	@echo "  test           Run pytest unit tests"
	@echo "  eval           Run the eval harness against eval/test_set/"
	@echo "  smoke-gemini   One-shot smoke test of the Gemini extractor"
	@echo "  smoke-openai   One-shot smoke test of the OpenAI extractor"
	@echo "  deploy         Push to main (Render auto-deploys)"
	@echo "  clean          Remove caches and the venv"

$(VENV)/bin/python:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip

install: $(VENV)/bin/python
	$(PIP) install -r requirements.txt

dev:
	$(UVICORN) app.main:app --reload --host $${HOST:-0.0.0.0} --port $${PORT:-8000}

test:
	$(PYTEST)

eval:
	$(PYTHON) -m eval.harness

smoke-gemini:
	$(PYTHON) scripts/smoke_gemini.py

smoke-openai:
	$(PYTHON) scripts/smoke_openai.py

deploy:
	@echo "Render auto-deploys on push to main. To trigger:"
	@echo "  git push origin main"

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache __pycache__ \
	       app/__pycache__ app/*/__pycache__ tests/__pycache__ \
	       scripts/__pycache__ eval/__pycache__
