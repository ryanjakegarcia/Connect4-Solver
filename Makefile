CXX := g++
CXXFLAGS := -std=c++17 -O2
PYTHON ?= .venv/bin/python

SOLVER := solver
SOLVER_SRC := src/solver.cpp

BACKFILL_SCORE_BIN := backfill_opening_book_scores
BACKFILL_SCORE_SRC := tools/backfill_opening_book.cpp

BACKFILL_MOVE_BIN := backfill_opening_book_moves
BACKFILL_MOVE_SRC := tools/backfill_opening_book_moves.cpp

.PHONY: help all solver tools clean venv ui-deps playwright-install ui-connect4 ui-vsai bridge-observe bridge-auto book-status

help:
	@echo "Targets:"
	@echo "  make all           Build solver and backfill binaries"
	@echo "  make solver        Build solver binary"
	@echo "  make tools         Build backfill utility binaries"
	@echo "  make venv          Create .venv"
	@echo "  make ui-deps       Install Python UI dependencies into .venv"
	@echo "  make playwright-install  Install Playwright browsers"
	@echo "  make ui-connect4   Run ui/connect4.py"
	@echo "  make ui-vsai       Run ui/connect4vsAI.py"
	@echo "  make bridge-observe Run browser bridge in papergames observe mode"
	@echo "  make bridge-auto   Run browser bridge in papergames auto mode (recommended flags)"
	@echo "  make book-status   Show unresolved move/score counts"
	@echo "  make clean         Remove local build artifacts"

all: solver tools

solver: $(SOLVER_SRC)
	$(CXX) $(CXXFLAGS) -o $(SOLVER) $(SOLVER_SRC)

tools: $(BACKFILL_SCORE_SRC) $(BACKFILL_MOVE_SRC)
	$(CXX) $(CXXFLAGS) -o $(BACKFILL_SCORE_BIN) $(BACKFILL_SCORE_SRC)
	$(CXX) $(CXXFLAGS) -o $(BACKFILL_MOVE_BIN) $(BACKFILL_MOVE_SRC)

venv:
	python3 -m venv .venv

ui-deps:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r ui/requirements.txt

playwright-install:
	$(PYTHON) -m playwright install chromium firefox

ui-connect4:
	$(PYTHON) ui/connect4.py

ui-vsai:
	$(PYTHON) ui/connect4vsAI.py

bridge-observe:
	$(PYTHON) ui/browser_bridge.py --site-mode papergames --browser firefox --persistent-profile --user-data-dir .pw-user-data-firefox --url https://papergames.io/en/connect4 --mode observe --player auto --window-width 1920 --window-height 1200

bridge-auto:
	$(PYTHON) ui/browser_bridge.py --site-mode papergames --browser firefox --persistent-profile --user-data-dir .pw-user-data-firefox --url https://papergames.io/en/connect4 --mode auto --player auto --weak --poll-ms 250 --post-game-wait-sec 5 --post-game-reload-sec 0

book-status:
	@awk '!/^#/ && NF>=3 {total++; miss_move=($$2=="-"); miss_score=($$3=="-"); if(miss_move) m++; if(miss_score) s++; if(miss_move||miss_score) either++; if(miss_move&&miss_score) both++;} END {printf "total=%d\nmissing_move=%d\nmissing_score=%d\nmissing_either=%d\nmissing_both=%d\n", total,m,s,either,both;}' data/opening_book.txt

clean:
	rm -f solver solver_check $(BACKFILL_SCORE_BIN) $(BACKFILL_MOVE_BIN)
