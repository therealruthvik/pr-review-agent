.PHONY: install preflight lint review

install:
	pip install -r requirements.txt

preflight:
	python agent/preflight.py

lint:
	python -m py_compile agent/*.py && echo "Syntax OK"

review:
	python -m agent.review_agent
