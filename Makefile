# Entry points for the checks this repo runs by hand.
#
# Recipe lines are tabs. `check` is read-only and finishes in seconds.
# `check-scorer` wipes the database and re-polls, so it refuses to run
# while the exit-test lock file is present.

PY = .venv/bin/python
LOCK = .exit-test-running
DB   = postgresql://nms:nms_dev_only@127.0.0.1:5432/nms
PSQL = psql "$(DB)" -P pager=off -tA

.DEFAULT_GOAL := help

help:
	@echo "Targets:"
	@echo "  help          This list. Runs nothing."
	@echo "  check         Read-only. Identity check, its selftest, a clean-tree"
	@echo "                assertion, then install-units.sh --check and"
	@echo "                host-setup.sh --check (both need sudo). Seconds."
	@echo "  check-scorer  DESTRUCTIVE. Resets the database, runs one poll cycle,"
	@echo "                then grades it with gate.py. Refuses while $(LOCK)"
	@echo "                exists. Stops nms-collector.timer for the"
	@echo "                destructive window and restores it, so it"
	@echo "                needs sudo."
	@echo "  state         Read-only, no sudo. Current machine state for pasting"
	@echo "                into a new session. Seconds."
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
	./scripts/check_scorer.sh

state:
	@echo "===== SOVEREIGN NMS — STATE $$(date -u '+%Y-%m-%d %H:%M:%S UTC') ====="
	@echo "-- git (the branch line reflects the last fetch, not the remote now)"
	@git status -sb | head -1
	@git log -1 --format='HEAD %h %s'
	@dirty="$$(git status --short)"; \
	if [ -n "$$dirty" ]; then echo "TREE DIRTY:"; echo "$$dirty"; \
	else echo "tree clean"; fi
	@echo "-- containers"
	@docker ps --filter name=nms- --format '{{.Names}}  {{.Status}}' \
	  || echo "  (docker unavailable)"
	@echo "-- units"
	@printf 'nms-api.service      %s\n' "$$(systemctl is-active nms-api.service)"
	@printf 'nms-collector.timer  %s\n' "$$(systemctl is-active nms-collector.timer)"
	@systemctl list-timers nms-collector --no-pager | sed -n '1,2p'
	@echo "-- schema"
	@$(PSQL) -c "SELECT 'migrations ' || count(*) || ', latest ' || max(filename) FROM schema_migration;" \
	  || echo "  (database unreachable)"
	@echo "-- data"
	@$(PSQL) -c "SELECT 'devices ' || count(*) FROM device;" \
	  || echo "  (database unreachable)"
	@$(PSQL) -c "SELECT 'links ' || state || ' ' || count(*) FROM link GROUP BY state;"
	@$(PSQL) -c "SELECT 'interfaces ' || count(*) FROM interface;"
	@$(PSQL) -c "SELECT 'last poll ' || coalesce(round(extract(epoch from now()-max(started_at))/60)::text,'never') || ' min ago' FROM discovery_run;"
	@echo "-- scorer (grades the simulated fleet ONLY — see open question 19)"
	@$(PY) scripts/score_topology.py \
	  | grep -E "RECALL|PRECISION|FALSE LINKS|port pairs correct" \
	  || echo "  (scorer failed)"
	@echo "-- identity"
	@$(PY) scripts/check_identity.py \
	  | grep -E "VIOLATIONS|ORPHAN PEER CHASSIS|^  info:" \
	  || echo "  (identity check failed)"
	@echo "-- next session, item 1 from HANDOFF.md"
	@sed -n '/^### Next session/,$$p' HANDOFF.md | grep -m1 '^1\. ' || echo "  (not found)"
	@echo "====================================================================="

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

.PHONY: help check check-scorer state lock unlock
