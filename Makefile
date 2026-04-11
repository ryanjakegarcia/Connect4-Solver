CXX := g++
CXXFLAGS := -std=c++17 -O2
WIN_CXX ?= x86_64-w64-mingw32-g++
WIN_CXXFLAGS ?= -std=c++17 -O2 -static -static-libgcc -static-libstdc++
WIN_EXT := .exe
PYTHON ?= .venv/bin/python
MODE ?= auto
BRIDGE_USERNAME ?=
BUILD_DIR ?= build

.DEFAULT_GOAL := help

SOLVER_BIN := $(BUILD_DIR)/solver
SOLVER_SRC := src/solver.cpp
SOLVER := solver
SOLVER_WIN_BIN := $(BUILD_DIR)/solver$(WIN_EXT)
SOLVER_WIN := solver$(WIN_EXT)

BACKFILL_SCORE_BIN := $(BUILD_DIR)/backfill_opening_book_scores
BACKFILL_SCORE_SRC := tools/backfill_opening_book.cpp
BACKFILL_SCORE := backfill_opening_book_scores
BACKFILL_SCORE_WIN_BIN := $(BUILD_DIR)/backfill_opening_book_scores$(WIN_EXT)
BACKFILL_SCORE_WIN := backfill_opening_book_scores$(WIN_EXT)

BACKFILL_MOVE_BIN := $(BUILD_DIR)/backfill_opening_book_moves
BACKFILL_MOVE_SRC := tools/backfill_opening_book_moves.cpp
BACKFILL_MOVE := backfill_opening_book_moves
BACKFILL_MOVE_WIN_BIN := $(BUILD_DIR)/backfill_opening_book_moves$(WIN_EXT)
BACKFILL_MOVE_WIN := backfill_opening_book_moves$(WIN_EXT)

BRIDGE_AUTO_SCRIPT := ui/launch_bridge.sh
BRIDGE_STANDBY_SCRIPT := ui/launch_bridge_standby.sh
BRIDGE_OBSERVE_SCRIPT := ui/launch_bridge_observe.sh
BRIDGE_ASSIST_SCRIPT := ui/launch_bridge_assist.sh
LOCAL_UI_SCRIPT := start-local
LOCAL_VSAI_SCRIPT := start-vsai

define BUILD_CPP_BIN
$1: $2 | $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) -o $$@ $$<
endef

$(eval $(call BUILD_CPP_BIN,$(SOLVER_BIN),$(SOLVER_SRC)))
$(eval $(call BUILD_CPP_BIN,$(BACKFILL_SCORE_BIN),$(BACKFILL_SCORE_SRC)))
$(eval $(call BUILD_CPP_BIN,$(BACKFILL_MOVE_BIN),$(BACKFILL_MOVE_SRC)))

$(SOLVER_WIN_BIN): $(SOLVER_SRC) | $(BUILD_DIR)
	$(WIN_CXX) $(WIN_CXXFLAGS) -o $@ $<

$(BACKFILL_SCORE_WIN_BIN): $(BACKFILL_SCORE_SRC) | $(BUILD_DIR)
	$(WIN_CXX) $(WIN_CXXFLAGS) -o $@ $<

$(BACKFILL_MOVE_WIN_BIN): $(BACKFILL_MOVE_SRC) | $(BUILD_DIR)
	$(WIN_CXX) $(WIN_CXXFLAGS) -o $@ $<

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

.PHONY: help all build-tools clean venv ui-deps local-ui local-vsai bridge book-status setup setup-ui setup-build \
	check-build-env check-build-env-win check-ui-env check-bridge-env check-local-launchers check-bridge-launchers \
	solver-win build-tools-win all-win \
	ci-setup ci-build ci-check ci-fast ci-full ci-smoke help-ci

help:
	@echo "Connect4-Bot Make Targets"
	@echo ""
	@echo "Build dir: $(BUILD_DIR)"
	@echo ""
	@echo "Build"
	@echo "  make all                 Build solver and backfill binaries"
	@echo "  make solver              Build solver binary (compat symlink at ./solver)"
	@echo "  make build-tools         Build backfill utility binaries"
	@echo "  make solver-win          Cross-build Windows solver binary (./solver.exe)"
	@echo "  make build-tools-win     Cross-build Windows backfill binaries"
	@echo "  make all-win             Cross-build Windows solver + backfill binaries"
	@echo ""
	@echo "Setup"
	@echo "  make setup               Full setup: venv + ui-deps + all"
	@echo "  make setup-ui            UI setup only: venv + ui-deps"
	@echo "  make setup-build         Build setup only: check-build-env + all"
	@echo ""
	@echo "CI"
	@echo "  make help-ci             Show CI targets only"
	@echo "  make ci-setup            CI setup: venv + ui-deps"
	@echo "  make ci-build            CI build: check-build-env + all"
	@echo "  make ci-fast             CI fast checks: build + local UI preflight"
	@echo "  make ci-check            CI checks: build + UI + bridge preflight"
	@echo "  make ci-full             CI full checks (alias for ci-check)"
	@echo "  make ci-smoke            CI smoke: build + solver move/status queries"
	@echo ""
	@echo "Python / UI"
	@echo "  make venv                Create .venv"
	@echo "  make ui-deps             Install Python UI dependencies + Playwright browsers"
	@echo "  make local-ui            Run local UI launcher script"
	@echo "  make local-vsai          Run VS-AI UI launcher script"
	@echo ""
	@echo "Browser Bridge"
	@echo "  make bridge MODE=auto    Run bridge launcher script by MODE (auto|standby|assist|observe)"
	@echo "  make bridge MODE=auto BRIDGE_USERNAME='Your Name'"
	@echo ""
	@echo "Diagnostics"
	@echo "  make check-build-env     Check C++ toolchain"
	@echo "  make check-ui-env        Check .venv Python and local UI launchers"
	@echo "  make check-bridge-env    Check bridge runtime prerequisites"
	@echo ""
	@echo "Data / Maintenance"
	@echo "  make book-status         Show unresolved move/score counts"
	@echo "  make clean               Remove local build artifacts"

help-ci:
	@echo "Connect4-Bot CI Targets"
	@echo "  make ci-setup            CI setup: venv + ui-deps"
	@echo "  make ci-build            CI build: check-build-env + all"
	@echo "  make ci-fast             CI fast checks: build + local UI preflight"
	@echo "  make ci-check            CI checks: build + UI + bridge preflight"
	@echo "  make ci-full             CI full checks (alias for ci-check)"
	@echo "  make ci-smoke            CI smoke: build + solver move/status queries"

# Build
all: solver build-tools

all-win: solver-win build-tools-win

setup: venv ui-deps all

setup-ui: venv ui-deps

setup-build: check-build-env all

ci-setup: setup-ui

ci-build: setup-build

ci-fast: ci-build check-ui-env

ci-check: ci-build check-ui-env check-bridge-env

ci-full: ci-check

ci-smoke: ci-build
	@move_out="$$(printf '4?\n' | ./$(SOLVER) 2>/dev/null || true)"; \
	case "$$move_out" in \
		[1-7]) ;; \
		*) echo "[ci-smoke] invalid move query response: '$$move_out'"; exit 1 ;; \
	esac; \
	status_out="$$(printf '4!\n' | ./$(SOLVER) 2>/dev/null || true)"; \
	case "$$status_out" in \
		ongoing|win1|win2|draw|invalid) ;; \
		*) echo "[ci-smoke] invalid status query response: '$$status_out'"; exit 1 ;; \
	esac; \
	echo "[ci-smoke] move=$$move_out status=$$status_out"

solver: check-build-env $(SOLVER_BIN)
	ln -sfn $(SOLVER_BIN) $(SOLVER)

solver-win: check-build-env-win $(SOLVER_WIN_BIN)
	ln -sfn $(SOLVER_WIN_BIN) $(SOLVER_WIN)

build-tools: check-build-env $(BACKFILL_SCORE_BIN) $(BACKFILL_MOVE_BIN)
	ln -sfn $(BACKFILL_SCORE_BIN) $(BACKFILL_SCORE)
	ln -sfn $(BACKFILL_MOVE_BIN) $(BACKFILL_MOVE)

build-tools-win: check-build-env-win $(BACKFILL_SCORE_WIN_BIN) $(BACKFILL_MOVE_WIN_BIN)
	ln -sfn $(BACKFILL_SCORE_WIN_BIN) $(BACKFILL_SCORE_WIN)
	ln -sfn $(BACKFILL_MOVE_WIN_BIN) $(BACKFILL_MOVE_WIN)

# Python / UI
venv:
	python3 -m venv .venv

ui-deps:
	@if [ ! -x "$(PYTHON)" ]; then \
		echo "[preflight] Python not found at $(PYTHON)"; \
		echo "[preflight] Run: make venv"; \
		exit 1; \
	fi
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r ui/requirements.txt
	$(PYTHON) -m playwright install chromium firefox

local-ui: check-ui-env
	./$(LOCAL_UI_SCRIPT)

local-vsai: check-ui-env
	./$(LOCAL_VSAI_SCRIPT)

# Browser Bridge
bridge: check-bridge-env
	@if [ "$(MODE)" = "auto" ]; then \
		if [ -n "$(BRIDGE_USERNAME)" ]; then \
			./$(BRIDGE_AUTO_SCRIPT) --our-username "$(BRIDGE_USERNAME)"; \
		else \
			./$(BRIDGE_AUTO_SCRIPT); \
		fi; \
	elif [ "$(MODE)" = "standby" ]; then \
		if [ -n "$(BRIDGE_USERNAME)" ]; then \
			./$(BRIDGE_STANDBY_SCRIPT) --our-username "$(BRIDGE_USERNAME)"; \
		else \
			./$(BRIDGE_STANDBY_SCRIPT); \
		fi; \
	elif [ "$(MODE)" = "assist" ]; then \
		if [ -n "$(BRIDGE_USERNAME)" ]; then \
			./$(BRIDGE_ASSIST_SCRIPT) --our-username "$(BRIDGE_USERNAME)"; \
		else \
			./$(BRIDGE_ASSIST_SCRIPT); \
		fi; \
	elif [ "$(MODE)" = "observe" ]; then \
		if [ -n "$(BRIDGE_USERNAME)" ]; then \
			./$(BRIDGE_OBSERVE_SCRIPT) --our-username "$(BRIDGE_USERNAME)"; \
		else \
			./$(BRIDGE_OBSERVE_SCRIPT); \
		fi; \
	else \
		echo "Unknown MODE='$(MODE)'. Use MODE=auto, MODE=standby, MODE=assist, or MODE=observe"; \
		exit 1; \
	fi

# Diagnostics
check-build-env:
	@command -v $(CXX) >/dev/null 2>&1 || { \
		echo "[preflight] Missing compiler: $(CXX)"; \
		echo "[preflight] Install g++ (C++17 capable) and retry."; \
		exit 1; \
	}

check-build-env-win:
	@command -v $(WIN_CXX) >/dev/null 2>&1 || { \
		echo "[preflight] Missing Windows cross-compiler: $(WIN_CXX)"; \
		echo "[preflight] Install mingw-w64 and retry (example: sudo apt-get install mingw-w64)."; \
		exit 1; \
	}

check-ui-env: check-local-launchers
	@if [ ! -x "$(PYTHON)" ]; then \
		echo "[preflight] Python not found at $(PYTHON)"; \
		echo "[preflight] Run: make venv"; \
		exit 1; \
	fi

check-local-launchers:
	@if [ ! -x "$(LOCAL_UI_SCRIPT)" ]; then \
		echo "[preflight] Missing executable launcher: $(LOCAL_UI_SCRIPT)"; \
		echo "[preflight] Run: chmod +x $(LOCAL_UI_SCRIPT)"; \
		exit 1; \
	fi
	@if [ ! -x "$(LOCAL_VSAI_SCRIPT)" ]; then \
		echo "[preflight] Missing executable launcher: $(LOCAL_VSAI_SCRIPT)"; \
		echo "[preflight] Run: chmod +x $(LOCAL_VSAI_SCRIPT)"; \
		exit 1; \
	fi

check-bridge-launchers:
	@if [ ! -x "$(BRIDGE_AUTO_SCRIPT)" ]; then \
		echo "[preflight] Missing executable launcher: $(BRIDGE_AUTO_SCRIPT)"; \
		echo "[preflight] Run: chmod +x $(BRIDGE_AUTO_SCRIPT)"; \
		exit 1; \
	fi
	@if [ ! -x "$(BRIDGE_STANDBY_SCRIPT)" ]; then \
		echo "[preflight] Missing executable launcher: $(BRIDGE_STANDBY_SCRIPT)"; \
		echo "[preflight] Run: chmod +x $(BRIDGE_STANDBY_SCRIPT)"; \
		exit 1; \
	fi
	@if [ ! -x "$(BRIDGE_ASSIST_SCRIPT)" ]; then \
		echo "[preflight] Missing executable launcher: $(BRIDGE_ASSIST_SCRIPT)"; \
		echo "[preflight] Run: chmod +x $(BRIDGE_ASSIST_SCRIPT)"; \
		exit 1; \
	fi
	@if [ ! -x "$(BRIDGE_OBSERVE_SCRIPT)" ]; then \
		echo "[preflight] Missing executable launcher: $(BRIDGE_OBSERVE_SCRIPT)"; \
		echo "[preflight] Run: chmod +x $(BRIDGE_OBSERVE_SCRIPT)"; \
		exit 1; \
	fi

check-bridge-env: check-ui-env check-bridge-launchers
	@if [ ! -x "$(SOLVER)" ] && [ ! -x "$(SOLVER_BIN)" ]; then \
		echo "[preflight] Solver binary not found."; \
		echo "[preflight] Run: make solver"; \
		exit 1; \
	fi
	@$(PYTHON) -c "import os, sys; from playwright.sync_api import sync_playwright; p = sync_playwright().start(); path = p.firefox.executable_path; p.stop(); sys.exit(0 if path and os.path.exists(path) else 1)" || { \
		echo "[preflight] Playwright Firefox browser is not ready."; \
		echo "[preflight] Run: make ui-deps"; \
		exit 1; \
	}

# Data / Maintenance
book-status:
	@awk '!/^#/ && NF>=3 {total++; miss_move=($$2=="-"); miss_score=($$3=="-"); if(miss_move) m++; if(miss_score) s++; if(miss_move||miss_score) either++; if(miss_move&&miss_score) both++;} END {printf "total=%d\nmissing_move=%d\nmissing_score=%d\nmissing_either=%d\nmissing_both=%d\n", total,m,s,either,both;}' data/opening_book.txt

clean:
	rm -f $(SOLVER) $(SOLVER_WIN) solver_check $(BACKFILL_SCORE) $(BACKFILL_MOVE) $(BACKFILL_SCORE_WIN) $(BACKFILL_MOVE_WIN)
	rm -rf $(BUILD_DIR)
