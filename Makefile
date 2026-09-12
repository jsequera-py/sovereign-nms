# Entry points for the checks this repo runs by hand.
#
# Recipe lines are tabs. `check` is read-only and finishes in seconds.
# `check-scorer` wipes the database and re-polls, so it refuses to run
# while the exit-test lock file is present.

PY = .venv/bin/python
LOCK = .exit-test-running

.DEFAULT_GOAL := help

help:
	@echo "Targets:"
	@echo "  help          This list. Runs nothing."
	@echo "  check         Read-only. Identity check, its selftest, a clean-tree"
	@echo "                assertion, then install-units.sh --check and"
	@echo "                host-setup.sh --check (both need sudo). Seconds."
	@echo "  check-scorer  DESTRUCTIVE. Resets the database, runs one poll cycle,"
	@echo "                then grades it with gate.py. Refuses while $(LOCK)"
	@echo "                exists."
	@echo "  lock          Create $(LOCK), blocking check-scorer for a 24-hour"
	@echo "                exit test."
	@echo "  unlock        Remove $(LOCK)."

check:
	$(PY) scripts/check_identity.py
	$(PY) scripts/check_identity.py --selftest
	@dirty="$$(git status --short)"; \
	if [ -n "$$dirty" ]; then \
		echo "check: FAIL — working tree is dirty:"; \
		echo "$$dirty"; \
		exit 1; \
	fi; \
	echo "check: working tree is clean"
	sudo ./deploy/install-units.sh --check
	sudo ./deploy/host-setup.sh --check

check-scorer:
	@if [ -e $(LOCK) ]; then \
		echo "check-scorer: refusing to run — $(LOCK) exists, a 24-hour exit test is in progress."; \
		exit 1; \
	fi
	./scripts/reset_data.sh --yes
	./scripts/poll_cycle.sh
	$(PY) scripts/gate.py

lock:
	@if [ -e $(LOCK) ]; then \
		echo "lock: $(LOCK) already exists — left as is."; \
	else \
		touch $(LOCK); \
		echo "lock: created $(LOCK) — check-scorer will refuse to run."; \
	fi

unlock:
	@if [ -e $(LOCK) ]; then \
		rm -f $(LOCK); \
		echo "unlock: removed $(LOCK) — check-scorer may run again."; \
	else \
		echo "unlock: $(LOCK) did not exist — nothing to do."; \
	fi

.PHONY: help check check-scorer lock unlock
