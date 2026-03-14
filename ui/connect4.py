#!/usr/bin/env python3
import atexit
from enum import Enum
import math
import os
import random
import subprocess
import sys

import numpy as np
import pygame

BLUE = (0, 0, 255)
BLACK = (0, 0, 0)
RED = (255, 0, 0)
YELLOW = (255, 255, 0)

ROW_COUNT = 6
COLUMN_COUNT = 7
WINDOW_LENGTH = 4

AUTO = 0
TURN_DELAY = 0
GAME_DELAY = 1000
DROP_SPEED = 30

P1 = 0
P2 = 1
TIE = 2

class Strategy(Enum):
    SOLVER = "solver"
    MINIMAX = "minimax"
    HEURISTIC = "heuristic"
    RANDOM = "random"


P1_STRATEGY = Strategy.SOLVER
P2_STRATEGY = Strategy.RANDOM
MINIMAX_DEPTH = 3

SCORE_4 = 5000
SCORE_3 = 10
SCORE_2 = 5

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOLVER_PATH = os.path.join(BASE_DIR, "..", "solver")
SCORES_PATH = os.path.join(BASE_DIR, "scores.txt")
USES_SOLVER = P1_STRATEGY is Strategy.SOLVER or P2_STRATEGY is Strategy.SOLVER


def create_board():
    return np.zeros((ROW_COUNT, COLUMN_COUNT), dtype=np.int8)


def drop_piece(board, row, col, piece):
    board[row][col] = piece + 1


def is_valid_location(board, col):
    return board[ROW_COUNT - 1][col] == 0


def get_next_open_row(board, col):
    for row in range(ROW_COUNT):
        if board[row][col] == 0:
            return row
    return None


def get_opponent(actor):
    return P2 if actor == P1 else P1


def winning_move(board, actor):
    actor_piece = actor + 1

    for col in range(COLUMN_COUNT - 3):
        for row in range(ROW_COUNT):
            if (
                board[row][col] == actor_piece
                and board[row][col + 1] == actor_piece
                and board[row][col + 2] == actor_piece
                and board[row][col + 3] == actor_piece
            ):
                return (
                    (int(col * SQUARESIZE + SQUARESIZE / 2), int(height - row * SQUARESIZE - SQUARESIZE / 2)),
                    (int((col + 3) * SQUARESIZE + SQUARESIZE / 2), int(height - row * SQUARESIZE - SQUARESIZE / 2)),
                )

    for col in range(COLUMN_COUNT):
        for row in range(ROW_COUNT - 3):
            if (
                board[row][col] == actor_piece
                and board[row + 1][col] == actor_piece
                and board[row + 2][col] == actor_piece
                and board[row + 3][col] == actor_piece
            ):
                return (
                    (int(col * SQUARESIZE + SQUARESIZE / 2), int(height - row * SQUARESIZE - SQUARESIZE / 2)),
                    (int(col * SQUARESIZE + SQUARESIZE / 2), int(height - (row + 3) * SQUARESIZE - SQUARESIZE / 2)),
                )

    for col in range(COLUMN_COUNT - 3):
        for row in range(ROW_COUNT - 3):
            if (
                board[row][col] == actor_piece
                and board[row + 1][col + 1] == actor_piece
                and board[row + 2][col + 2] == actor_piece
                and board[row + 3][col + 3] == actor_piece
            ):
                return (
                    (int(col * SQUARESIZE + SQUARESIZE / 2), int(height - row * SQUARESIZE - SQUARESIZE / 2)),
                    (int((col + 3) * SQUARESIZE + SQUARESIZE / 2), int(height - (row + 3) * SQUARESIZE - SQUARESIZE / 2)),
                )

    for col in range(COLUMN_COUNT - 3):
        for row in range(3, ROW_COUNT):
            if (
                board[row][col] == actor_piece
                and board[row - 1][col + 1] == actor_piece
                and board[row - 2][col + 2] == actor_piece
                and board[row - 3][col + 3] == actor_piece
            ):
                return (
                    (int(col * SQUARESIZE + SQUARESIZE / 2), int(height - row * SQUARESIZE - SQUARESIZE / 2)),
                    (int((col + 3) * SQUARESIZE + SQUARESIZE / 2), int(height - (row - 3) * SQUARESIZE - SQUARESIZE / 2)),
                )

    return None


def is_winning_board(board, piece):
    for col in range(COLUMN_COUNT - 3):
        for row in range(ROW_COUNT):
            if (
                board[row][col] == piece
                and board[row][col + 1] == piece
                and board[row][col + 2] == piece
                and board[row][col + 3] == piece
            ):
                return True

    for col in range(COLUMN_COUNT):
        for row in range(ROW_COUNT - 3):
            if (
                board[row][col] == piece
                and board[row + 1][col] == piece
                and board[row + 2][col] == piece
                and board[row + 3][col] == piece
            ):
                return True

    for col in range(COLUMN_COUNT - 3):
        for row in range(ROW_COUNT - 3):
            if (
                board[row][col] == piece
                and board[row + 1][col + 1] == piece
                and board[row + 2][col + 2] == piece
                and board[row + 3][col + 3] == piece
            ):
                return True

    for col in range(COLUMN_COUNT - 3):
        for row in range(3, ROW_COUNT):
            if (
                board[row][col] == piece
                and board[row - 1][col + 1] == piece
                and board[row - 2][col + 2] == piece
                and board[row - 3][col + 3] == piece
            ):
                return True

    return False


def draw_board(board):
    for col in range(COLUMN_COUNT):
        for row in range(ROW_COUNT):
            pygame.draw.rect(screen, BLUE, (col * SQUARESIZE, row * SQUARESIZE + SQUARESIZE, SQUARESIZE, SQUARESIZE))
            pygame.draw.circle(screen, BLACK, (int(col * SQUARESIZE + SQUARESIZE / 2), int(row * SQUARESIZE + SQUARESIZE + SQUARESIZE / 2)), RADIUS)

    for col in range(COLUMN_COUNT):
        for row in range(ROW_COUNT):
            if board[row][col] == 1:
                pygame.draw.circle(screen, RED, (int(col * SQUARESIZE + SQUARESIZE / 2), height - int(row * SQUARESIZE + SQUARESIZE / 2)), RADIUS)
            elif board[row][col] == 2:
                pygame.draw.circle(screen, YELLOW, (int(col * SQUARESIZE + SQUARESIZE / 2), height - int(row * SQUARESIZE + SQUARESIZE / 2)), RADIUS)
    pygame.display.update()


def animate_drop(board, col, row, piece):
    target_y = height - int(row * SQUARESIZE + SQUARESIZE / 2)
    pos_x = int(col * SQUARESIZE + SQUARESIZE / 2)
    pos_y = int(SQUARESIZE / 2)
    color = RED if piece == P1 else YELLOW

    while pos_y < target_y:
        for animation_event in pygame.event.get([pygame.QUIT, pygame.KEYDOWN]):
            if animation_event.type == pygame.QUIT:
                sys.exit()
            if animation_event.type == pygame.KEYDOWN and animation_event.key == pygame.K_ESCAPE:
                sys.exit()

        pygame.draw.rect(screen, BLACK, (0, 0, width, SQUARESIZE))
        draw_board(board)
        pygame.draw.circle(screen, color, (pos_x, int(pos_y)), RADIUS)
        pygame.display.update()

        pos_y = min(pos_y + DROP_SPEED, target_y)
        pygame.time.delay(20)


def evaluate_position(board, actor):
    score = 0

    center_array = [int(piece) for piece in list(board[:, COLUMN_COUNT // 2])]
    center_count = center_array.count(actor + 1)
    score += center_count * 3

    for row in range(ROW_COUNT):
        row_array = [int(piece) for piece in list(board[row, :])]
        for col in range(COLUMN_COUNT - WINDOW_LENGTH + 1):
            score += evaluate_window(row_array[col:col + WINDOW_LENGTH], actor)

    for col in range(COLUMN_COUNT):
        col_array = [int(piece) for piece in list(board[:, col])]
        for row in range(ROW_COUNT - WINDOW_LENGTH + 1):
            score += evaluate_window(col_array[row:row + WINDOW_LENGTH], actor)

    for row in range(ROW_COUNT - WINDOW_LENGTH + 1):
        for col in range(COLUMN_COUNT - WINDOW_LENGTH + 1):
            window = [board[row + offset][col + offset] for offset in range(WINDOW_LENGTH)]
            score += evaluate_window(window, actor)

    for row in range(ROW_COUNT - WINDOW_LENGTH + 1):
        for col in range(COLUMN_COUNT - WINDOW_LENGTH + 1):
            window = [board[row + (WINDOW_LENGTH - 1) - offset][col + offset] for offset in range(WINDOW_LENGTH)]
            score += evaluate_window(window, actor)

    return score


def evaluate_window(window, actor):
    score = 0
    actor_piece = actor + 1
    opponent_piece = get_opponent(actor) + 1

    if window.count(actor_piece) == 4:
        score += SCORE_4
    elif window.count(actor_piece) == 3 and window.count(0) == 1:
        score += SCORE_3
    elif window.count(actor_piece) == 2 and window.count(0) == 2:
        score += SCORE_2

    if window.count(opponent_piece) == 3 and window.count(0) == 1:
        score -= SCORE_3 * 10

    return score


def get_valid_locations(board):
    return [col for col in range(COLUMN_COUNT) if is_valid_location(board, col)]


def get_immediate_winning_cols(board, actor):
    winning_cols = []
    for col in get_valid_locations(board):
        row = get_next_open_row(board, col)
        if row is None:
            continue
        child_board = board.copy()
        drop_piece(child_board, row, col, actor)
        if is_winning_board(child_board, actor + 1):
            winning_cols.append(col)
    return winning_cols


def order_moves(valid_locations):
    center_col = COLUMN_COUNT // 2
    return sorted(valid_locations, key=lambda col: abs(col - center_col))


def pick_best_move(board, actor):
    valid_locations = get_valid_locations(board)
    if not valid_locations:
        return None

    best_score = -math.inf
    best_col = random.choice(valid_locations)
    for col in valid_locations:
        row = get_next_open_row(board, col)
        temp_board = board.copy()
        drop_piece(temp_board, row, col, actor)
        score = evaluate_position(temp_board, actor)
        if score > best_score:
            best_score = score
            best_col = col

    return best_col


def write_score_to_file(winner):
    if not os.path.exists(SCORES_PATH):
        with open(SCORES_PATH, "w+", encoding="utf-8") as score_file:
            score_file.writelines(["0\n", "0\n", "0\n"])

    with open(SCORES_PATH, "r+", encoding="utf-8") as score_file:
        scores = score_file.readlines()
        while len(scores) <= winner:
            scores.append("0\n")

        scores[winner] = str(int(scores[winner].strip()) + 1) + "\n"

        score_file.seek(0)
        score_file.writelines(scores)
        score_file.truncate()


def is_terminal_node(board):
    return is_winning_board(board, P1 + 1) or is_winning_board(board, P2 + 1) or not get_valid_locations(board)


def minimax(board, depth, alpha, beta, current_actor, maximizing_actor):
    valid_locations = get_valid_locations(board)
    opponent = get_opponent(maximizing_actor)

    if depth == 0 or is_terminal_node(board):
        if is_winning_board(board, maximizing_actor + 1):
            return None, 10_000_000_000_000
        if is_winning_board(board, opponent + 1):
            return None, -10_000_000_000_000
        if not valid_locations:
            return None, 0
        return None, evaluate_position(board, maximizing_actor)

    ordered_locations = order_moves(valid_locations)
    if current_actor == maximizing_actor:
        value = -math.inf
        best_column = ordered_locations[0]
        for col in ordered_locations:
            row = get_next_open_row(board, col)
            child_board = board.copy()
            drop_piece(child_board, row, col, current_actor)
            _, new_value = minimax(child_board, depth - 1, alpha, beta, get_opponent(current_actor), maximizing_actor)
            if new_value > value:
                value = new_value
                best_column = col
            alpha = max(alpha, value)
            if alpha >= beta:
                break
        return best_column, value

    value = math.inf
    best_column = ordered_locations[0]
    for col in ordered_locations:
        row = get_next_open_row(board, col)
        child_board = board.copy()
        drop_piece(child_board, row, col, current_actor)
        _, new_value = minimax(child_board, depth - 1, alpha, beta, get_opponent(current_actor), maximizing_actor)
        if new_value < value:
            value = new_value
            best_column = col
        beta = min(beta, value)
        if alpha >= beta:
            break
    return best_column, value


def start_solver_process():
    if not USES_SOLVER:
        return None

    try:
        return subprocess.Popen(
            [SOLVER_PATH],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        print(f"Solver unavailable at {SOLVER_PATH}: {exc}")
        return None


solver_proc = start_solver_process()


def cleanup_solver_process():
    if solver_proc is not None and solver_proc.poll() is None:
        solver_proc.terminate()


atexit.register(cleanup_solver_process)


def get_solver_move(move_sequence, board, actor):
    valid_cols = get_valid_locations(board)
    if not valid_cols:
        return None

    winning_cols = get_immediate_winning_cols(board, actor)
    if winning_cols:
        return order_moves(winning_cols)[0]

    blocking_cols = get_immediate_winning_cols(board, get_opponent(actor))
    if blocking_cols:
        return order_moves(blocking_cols)[0]

    if solver_proc is not None and solver_proc.stdin is not None and solver_proc.stdout is not None:
        query = move_sequence + "?"
        solver_proc.stdin.write(query + "\n")
        solver_proc.stdin.flush()
        response = solver_proc.stdout.readline().strip()
        try:
            col = int(response) - 1
            if col in valid_cols:
                return col
        except ValueError:
            pass

    fallback_col, _ = minimax(board, MINIMAX_DEPTH, -math.inf, math.inf, actor, actor)
    if fallback_col is not None:
        return fallback_col
    return order_moves(valid_cols)[0]


def get_ai_move(board, actor, move_sequence):
    strategy = P1_STRATEGY if actor == P1 else P2_STRATEGY

    if strategy is Strategy.SOLVER:
        return get_solver_move(move_sequence, board, actor)
    if strategy is Strategy.MINIMAX:
        col, _ = minimax(board, MINIMAX_DEPTH, -math.inf, math.inf, actor, actor)
        return col
    if strategy is Strategy.HEURISTIC:
        return pick_best_move(board, actor)
    if strategy is Strategy.RANDOM:
        valid_locations = get_valid_locations(board)
        return random.choice(valid_locations) if valid_locations else None

    raise ValueError(f"Unknown strategy: {strategy.value}")


def toggle_auto_mode():
    global AUTO

    display_text = "Auto mode: " + ("ON" if AUTO == 0 else "OFF")
    label = game_font.render(display_text, 1, BLUE)
    screen.blit(label, (40, 10))
    pygame.display.update()
    print(display_text)
    AUTO = 1 - AUTO


def update_delays(key):
    global TURN_DELAY, GAME_DELAY

    if key == pygame.K_UP:
        TURN_DELAY = min(TURN_DELAY + 50, 1000)
        print(f"Turn delay: {TURN_DELAY}ms")
    if key == pygame.K_DOWN:
        TURN_DELAY = max(TURN_DELAY - 50, 0)
        print(f"Turn delay: {TURN_DELAY}ms")
    if key == pygame.K_RIGHT:
        GAME_DELAY = min(GAME_DELAY + 100, 5000)
        print(f"Game delay: {GAME_DELAY}ms")
    if key == pygame.K_LEFT:
        GAME_DELAY = max(GAME_DELAY - 100, 0)
        print(f"Game delay: {GAME_DELAY}ms")


def reset_round():
    new_board = create_board()
    pygame.draw.rect(screen, BLACK, (0, 0, width, height))
    draw_board(new_board)
    return new_board, False, "", random.randint(P1, P2)


board = create_board()
game_over = False
move_sequence = ""

pygame.init()

SQUARESIZE = 100
width = COLUMN_COUNT * SQUARESIZE
height = (ROW_COUNT + 1) * SQUARESIZE
size = (width, height)
RADIUS = int(SQUARESIZE / 2 - 5)

screen = pygame.display.set_mode(size)
draw_board(board)
pygame.display.update()

turn = random.randint(P1, P2)
game_font = pygame.font.SysFont("monospace", 75)


while True:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            sys.exit()

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                sys.exit()
            if event.key == pygame.K_SPACE:
                toggle_auto_mode()
            update_delays(event.key)

    if not game_over:
        col = get_ai_move(board, turn, move_sequence)
        if col is not None and is_valid_location(board, col):
            row = get_next_open_row(board, col)
            pygame.time.wait(TURN_DELAY)
            animate_drop(board, col, row, turn)
            drop_piece(board, row, col, turn)
            move_sequence += str(col + 1)

            win_line = winning_move(board, turn)
            if win_line:
                winner_text = "Red Wins!!" if turn == P1 else "Yellow wins!!"
                winner_color = RED if turn == P1 else YELLOW
                draw_board(board)
                pygame.draw.line(screen, BLACK, win_line[0], win_line[1], 5)
                label = game_font.render(winner_text, 1, winner_color)
                screen.blit(label, (40, 10))
                pygame.display.update()
                pygame.time.wait(GAME_DELAY)
                write_score_to_file(turn)
                game_over = True
            else:
                draw_board(board)
                turn = get_opponent(turn)

    if not get_valid_locations(board) and not game_over:
        label = game_font.render("It's a tie!", 1, BLUE)
        screen.blit(label, (40, 10))
        pygame.display.update()
        pygame.time.wait(GAME_DELAY)
        write_score_to_file(TIE)
        game_over = True

    if game_over:
        if AUTO == 1:
            board, game_over, move_sequence, turn = reset_round()
        else:
            waiting = True
            while waiting:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        sys.exit()
                    if event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            sys.exit()
                        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                            board, game_over, move_sequence, turn = reset_round()
                            waiting = False
                        if event.key == pygame.K_SPACE:
                            toggle_auto_mode()
                            board, game_over, move_sequence, turn = reset_round()
                            waiting = False
                        update_delays(event.key)

    if get_valid_locations(board) == []:
        display_text = "It's a tie!"
        label = game_font.render(display_text, 1, BLUE)
        screen.blit(label, (40,10))
        pygame.display.update()
        pygame.time.wait(GAME_DELAY)
        write_score_to_file(TIE)
        board = create_board()
        game_over = False
        turn = random.randint(P1, P2)
        pygame.draw.rect(screen, BLACK, (0, 0, width, height))
        draw_board(board)

    if game_over:
        if AUTO == 1:
            board = create_board()
            game_over = False
            turn = random.randint(P1, P2)
            pygame.draw.rect(screen, BLACK, (0, 0, width, height))
            draw_board(board)
        else:
            waiting = True
            while waiting:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        sys.exit()
                    if event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            sys.exit()
                        if event.key == pygame.K_KP_ENTER or event.key == pygame.K_RETURN:
                            board = create_board()
                            game_over = False
                            turn = random.randint(P1, P2)
                            pygame.draw.rect(screen, BLACK, (0, 0, width, height))
                            draw_board(board)
                            waiting = False
                        if event.key == pygame.K_SPACE:
                            display_text = "Auto mode: " + ("ON" if AUTO == 0 else "OFF")
                            label = game_font.render(display_text, 1, BLUE)
                            screen.blit(label, (40,10))
                            print("Auto mode: " + ("ON" if AUTO == 0 else "OFF"))
                            AUTO = 1 - AUTO
                            board = create_board()
                            game_over = False
                            turn = random.randint(P1, P2)
                            pygame.draw.rect(screen, BLACK, (0, 0, width, height))
                            draw_board(board)
                            waiting = False
                        if event.key == pygame.K_UP:
                            TURN_DELAY = min(TURN_DELAY + 50, 1000)
                            print("Turn delay: " + str(TURN_DELAY) + "ms")
                        if event.key == pygame.K_DOWN:
                            TURN_DELAY = max(TURN_DELAY - 50, 0)
                            print("Turn delay: " + str(TURN_DELAY) + "ms")
                        if event.key == pygame.K_RIGHT:
                            GAME_DELAY = min(GAME_DELAY + 100, 5000)
                            print("Game delay: " + str(GAME_DELAY) + "ms")
                        if event.key == pygame.K_LEFT:
                            GAME_DELAY = max(GAME_DELAY - 100, 0)
                            print("Game delay: " + str(GAME_DELAY) + "ms")
            
