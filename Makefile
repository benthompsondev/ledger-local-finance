PYTHON ?= python3
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python

.PHONY: setup demo run check smoke share clean

setup:
	$(PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements.txt

demo:
	$(VENV_PYTHON) -m scripts.create_demo_data --force
	LEDGER_DEMO_DB=1 $(VENV_PYTHON) -m streamlit run app.py --server.address 127.0.0.1

run:
	$(VENV_PYTHON) -m streamlit run app.py --server.address 127.0.0.1

check:
	$(VENV_PYTHON) -m scripts.doctor
	$(VENV_PYTHON) -m compileall -q app.py pages utils parsers scripts components

smoke:
	$(VENV_PYTHON) -m scripts.smoke_test

share:
	$(VENV_PYTHON) -m scripts.make_share_zip

clean:
	rm -rf __pycache__ pages/__pycache__ utils/__pycache__ parsers/__pycache__ scripts/__pycache__ components/__pycache__
