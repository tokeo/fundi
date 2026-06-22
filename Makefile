.PHONY: help clean venv outdated dev prod doc test fmt lint docker sdist wheel dist-upload

help:
	@echo
	@echo
	@echo "___________________________________________________"
	@echo
	@echo "Use make with proper rule:"
	@echo
	@echo "  clean - remove all cache files and temps"
	@echo "  venv - create venv"
	@echo "  outdated - check outdated packages"
	@echo "  dev - install dev components"
	@echo "  prod - install prod components"
	@echo "  doc - create and (serve) documentation"
	@echo "  test (debug=1) files=tests/file - run tests"
	@echo "  fmt (source=module) = run formatter"
	@echo "  lint (source=module) = run linter"
	@echo "  sdist - create source tgz"
	@echo "  wheel - create installation wheel"
	@echo

clean:
	find . -name '*.py[co]' -delete
	find . -type d -name '__pycache__' -delete
	find . -type d -name '__pycache__' | sort --reverse | xargs rm -rfv
	rm -rf .pytest_cache .coverage coverage-report
	rm -rf html
	rm -rf tmp
	rm -rf build/
	mkdir -p tmp/tests
	touch tmp/tests/.gitkeep

venv:
	@if [ "0${VIRTUAL_ENV}" != "0" ]; then echo "Please deactivate venv before continue!"; exit 1; fi
	python -m venv --prompt '> fundi <' .venv
	.venv/bin/pip install --upgrade pip
	@echo
	@echo
	@echo "___________________________________________________"
	@echo
	@echo
	@echo "VENV Setup Complete!"
	@echo "  activate now using: \`source .venv/bin/activate\`"
	@echo
	@echo "With active venv install the app:"
	@echo "  \`make dev\` or \`make prod\`"
	@echo

outdated:
	@if [ "0${VIRTUAL_ENV}"${no_venv} == "0" ]; then echo "No venv activated! Add no_venv=1 to enforce make."; exit 1; fi
	pip --disable-pip-version-check list --outdated

dev:
	@if [ "0${VIRTUAL_ENV}"${no_venv} == "0" ]; then echo "No venv activated! Add no_venv=1 to enforce make."; exit 1; fi
	pip install --upgrade pip
	pip install -e .
	pip install -e .[dev]

prod:
	@if [ "0${VIRTUAL_ENV}"${no_venv} == "0" ]; then echo "No venv activated! Add no_venv=1 to enforce make."; exit 1; fi
	pip install --upgrade pip
	pip install -e .

doc:
	@if [ "0${VIRTUAL_ENV}"${no_venv} == "0" ]; then echo "No venv activated! Add no_venv=1 to enforce make."; exit 1; fi
	tokeo pdoc render --clean --serve tokeo.core.ai tests

# check for verbosity
ifdef verbose
verbosity=${verbose}
else
verbosity=v
endif

# check for log-level and enable print statement outputs on debug
ifdef debug
allow_print=-s
log_level=--log-cli-level=$(debug)0
else
allow_print=
log_level=
endif

# check for test with coverage reports
ifdef cov
cov_report=--cov=tokeo --cov-report=term --cov-report=html:coverage-report
else
cov_report=
endif

# check for files
ifndef files
files=tests/
endif

# limit the tests to run by files and tests filters
# make test files=test_logging.py tests=logging debug=1
test:
	@if [ "0${VIRTUAL_ENV}"${no_venv} == "0" ]; then echo "No venv activated! Add no_venv=1 to enforce make."; exit 1; fi
	rm -rf tmp/tests
	mkdir -p tmp/tests
	touch tmp/tests/.gitkeep
	CEMENT_LOG=0 \
	python -m pytest \
		-${verbosity} \
		$(allow_print) \
		$(cov_report) \
		--basetemp=tmp/tests \
		$(log_level) \
		-k "$(tests)" \
		$(files)

# check for files
ifndef sources
sources=tokeo docs tests
endif

fmt:
	@if [ "0${VIRTUAL_ENV}"${no_venv} == "0" ]; then echo "No venv activated! Add no_venv=1 to enforce make."; exit 1; fi
	# align with https://google.github.io/styleguide/pyguide.html
	pyink --pyink-use-majority-quotes --line-length 139 --include "\.py" --exclude="/(\.git|__pycache__)/" $(sources)

lint:
	@if [ "0${VIRTUAL_ENV}"${no_venv} == "0" ]; then echo "No venv activated! Add no_venv=1 to enforce make."; exit 1; fi
	# align with https://google.github.io/styleguide/pyguide.html
	flake8 --max-line-length 140 --max-doc-length 84 --extend-ignore "" --exclude "*/grpc/proto/*_pb2*.py,.git,__pycache__" $(sources)

sdist: clean
	python -m build --sdist

wheel: clean
	python -m build --wheel

dist-upload:
	twine upload dist/*
