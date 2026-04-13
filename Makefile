.PHONY: install scrape test lint info serve clean rebuild-api snapshot

install:
	pip install -e ".[dev]"

scrape:
	rainfall scrape

test:
	pytest -v

lint:
	ruff check src tests

info:
	rainfall info

rebuild-api:
	rainfall rebuild-api

snapshot:
	rainfall snapshot

# Serve the dashboard locally for development (http://localhost:8000)
serve:
	cd docs && python -m http.server 8000

clean:
	rm -rf .pytest_cache .ruff_cache build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
