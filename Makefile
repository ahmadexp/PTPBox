.PHONY: dev build standalone test lint check install uninstall

dev:
	npm run dev

build:
	npm run build

standalone:
	npm run build:standalone

test:
	npm test

lint:
	npm run lint

check: lint test standalone
	python3 -m py_compile agent/ptpbox_agent.py scripts/ptpboxctl.py
	python3 -m unittest discover -s tests -p 'test_*.py'
	bash -n scripts/install-host.sh scripts/uninstall-host.sh

install: standalone
	sudo PTPBOX_USER="$$(id -un)" bash scripts/install-host.sh

uninstall:
	sudo bash scripts/uninstall-host.sh
