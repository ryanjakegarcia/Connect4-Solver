#!/usr/bin/env python3
import random
import numpy as np
import pygame
import sys
import os
import subprocess
import atexit

BLUE = (0, 0, 255)
BLACK = (0, 0, 0)
RED = (255, 0, 0)
YELLOW = (255, 255, 0)

ROW_COUNT = 6
COLUMN_COUNT = 7

AUTO = 1
TURN_DELAY = 500
GAME_DELAY = 1000
DROP_SPEED = 30

PLAYER = 0
AI = 1
TIE = 2


def create_board():
    return np.zeros((ROW_COUNT, COLUMN_COUNT), dtype=np.int8)

def drop_piece(board, row, col, piece): #piece position is calculated by get_next_open_row function
    board[row][col] = piece + 1

def is_valid_location(board, col):
    return board[ROW_COUNT-1][col] == 0 #if top row is empty, we can move there

def get_next_open_row(board, col): #board in memory is upside down, so we start from 0 (the bottom row) and go up until we find an empty spot
    for r in range(ROW_COUNT):
        if board[r][col] == 0:
            return r

def winning_move(board, actor_piece):
    actor_piece += 1
    #print("Checking for win for actor: " + str(actor_piece))
    # Check horizontal locations for win
    for c in range(COLUMN_COUNT-3):
        for r in range(ROW_COUNT):
            if board[r][c] == actor_piece and board[r][c+1] == actor_piece and board[r][c+2] == actor_piece and board[r][c+3] == actor_piece:
                return (
                    (int(c*SQUARESIZE+SQUARESIZE/2), int(height - r*SQUARESIZE-SQUARESIZE/2)),
                    (int((c+3)*SQUARESIZE+SQUARESIZE/2), int(height - r*SQUARESIZE-SQUARESIZE/2))
                )

    # Check vertical locations for win
    for c in range(COLUMN_COUNT):
        for r in range(ROW_COUNT-3):
            if board[r][c] == actor_piece and board[r+1][c] == actor_piece and board[r+2][c] == actor_piece and board[r+3][c] == actor_piece:
                return (
                    (int(c*SQUARESIZE+SQUARESIZE/2), int(height - r*SQUARESIZE-SQUARESIZE/2)),
                    (int(c*SQUARESIZE+SQUARESIZE/2), int(height - (r+3)*SQUARESIZE-SQUARESIZE/2))
                )

    # Check positively sloped diagonals
    for c in range(COLUMN_COUNT-3):
        for r in range(ROW_COUNT-3):
            if board[r][c] == actor_piece and board[r+1][c+1] == actor_piece and board[r+2][c+2] == actor_piece and board[r+3][c+3] == actor_piece:
                return (
                    (int(c*SQUARESIZE+SQUARESIZE/2), int(height - r*SQUARESIZE-SQUARESIZE/2)),
                    (int((c+3)*SQUARESIZE+SQUARESIZE/2), int(height - (r+3)*SQUARESIZE-SQUARESIZE/2))
                )

    # Check negatively sloped diagonals
    for c in range(COLUMN_COUNT-3):
        for r in range(3, ROW_COUNT):
            if board[r][c] == actor_piece and board[r-1][c+1] == actor_piece and board[r-2][c+2] == actor_piece and board[r-3][c+3] == actor_piece:
                return (
                    (int(c*SQUARESIZE+SQUARESIZE/2), int(height - r*SQUARESIZE-SQUARESIZE/2)),
                    (int((c+3)*SQUARESIZE+SQUARESIZE/2), int(height - (r-3)*SQUARESIZE-SQUARESIZE/2))
                )

    return None

def is_winning_board(board, piece):
    for c in range(COLUMN_COUNT - 3):
        for r in range(ROW_COUNT):
            if board[r][c] == piece and board[r][c+1] == piece and board[r][c+2] == piece and board[r][c+3] == piece:
                return True

    for c in range(COLUMN_COUNT):
        for r in range(ROW_COUNT - 3):
            if board[r][c] == piece and board[r+1][c] == piece and board[r+2][c] == piece and board[r+3][c] == piece:
                return True

    for c in range(COLUMN_COUNT - 3):
        for r in range(ROW_COUNT - 3):
            if board[r][c] == piece and board[r+1][c+1] == piece and board[r+2][c+2] == piece and board[r+3][c+3] == piece:
                return True

    for c in range(COLUMN_COUNT - 3):
        for r in range(3, ROW_COUNT):
            if board[r][c] == piece and board[r-1][c+1] == piece and board[r-2][c+2] == piece and board[r-3][c+3] == piece:
                return True

    return False

def get_immediate_winning_cols(board, actor):
    actor_piece = actor + 1
    winning_cols = []
    for col in get_valid_locations(board):
        row = get_next_open_row(board, col)
        child_board = board.copy()
        drop_piece(child_board, row, col, actor)
        if is_winning_board(child_board, actor_piece):
            winning_cols.append(col)
    return winning_cols
            
def draw_board(board):
    for c in range(COLUMN_COUNT):
        for r in range(ROW_COUNT):
            pygame.draw.rect(screen, BLUE, (c*SQUARESIZE, r*SQUARESIZE+SQUARESIZE, SQUARESIZE, SQUARESIZE))
            pygame.draw.circle(screen, BLACK, (int(c*SQUARESIZE+SQUARESIZE/2), int(r*SQUARESIZE+SQUARESIZE+SQUARESIZE/2)), RADIUS)
    
    for c in range(COLUMN_COUNT):
        for r in range(ROW_COUNT):
            if board[r][c] == 1:
                pygame.draw.circle(screen, RED, (int(c*SQUARESIZE+SQUARESIZE/2), height - int(r*SQUARESIZE+SQUARESIZE/2)), RADIUS)
            elif board[r][c] == 2: 
                pygame.draw.circle(screen, YELLOW, (int(c*SQUARESIZE+SQUARESIZE/2), height - int(r*SQUARESIZE+SQUARESIZE/2)), RADIUS)
    pygame.display.update()

def animate_drop(board, col, row, piece):
    target_y = height - int(row*SQUARESIZE+SQUARESIZE/2)
    pos_x = int(col*SQUARESIZE+SQUARESIZE/2)
    pos_y = int(SQUARESIZE/2)
    color = RED if piece == PLAYER else YELLOW

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

def get_valid_locations(board):
    valid_locations = []
    for col in range(COLUMN_COUNT):
        if is_valid_location(board, col):
            valid_locations.append(col)
    return valid_locations

def order_moves(valid_locations):
    center_col = COLUMN_COUNT // 2
    return sorted(valid_locations, key=lambda col: abs(col - center_col))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCORES_VS_AI_PATH = os.path.join(BASE_DIR, "scoresvsAI.txt")

def write_score_to_file(winner):
    if not os.path.exists(SCORES_VS_AI_PATH):
        with open(SCORES_VS_AI_PATH, "w+") as score_file:
            score_file.writelines(["0\n", "0\n", "0\n"])

    with open(SCORES_VS_AI_PATH, "r+") as score_file:
        scores = score_file.readlines()
        while len(scores) <= winner:
            scores.append("0\n")

        score = int(scores[winner].strip()) + 1
        scores[winner] = str(score) + "\n"

        score_file.seek(0)
        score_file.writelines(scores)
        score_file.truncate()

SOLVER_PATH = os.path.join(BASE_DIR, "..", "solver")

solver_proc = subprocess.Popen(
    [SOLVER_PATH],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True,
    bufsize=1
)

def cleanup_solver_process():
    if solver_proc.poll() is None:
        solver_proc.terminate()

atexit.register(cleanup_solver_process)

def get_ai_move_from_solver(move_sequence, board):
    """Ask the C++ solver for the best move. Sends one query per turn."""
    valid_cols = get_valid_locations(board)
    if not valid_cols:
        return None

    # Tactical guardrails before consulting the solver.
    # 1) Always take a forced win.
    ai_winning_cols = get_immediate_winning_cols(board, AI)
    if ai_winning_cols:
        return order_moves(ai_winning_cols)[0]

    # 2) Always block opponent's immediate win.
    player_winning_cols = get_immediate_winning_cols(board, PLAYER)
    if player_winning_cols:
        return order_moves(player_winning_cols)[0]

    query = move_sequence + "?"
    solver_stdin = solver_proc.stdin
    solver_stdout = solver_proc.stdout
    if solver_stdin is None or solver_stdout is None:
        return order_moves(valid_cols)[0]

    solver_stdin.write(query + "\n")
    solver_stdin.flush()
    response = solver_stdout.readline().strip()
    try:
        col = int(response) - 1  # solver returns 1-based column
        if is_valid_location(board, col):
            return col
    except ValueError:
        pass

    # Fallback: center-first valid column
    return order_moves(valid_cols)[0]

def toggle_auto_mode():
    global AUTO

    display_text = "Auto mode: " + ("ON" if AUTO == 0 else "OFF")
    label = game_font.render(display_text, 1, BLUE)
    screen.blit(label, (40,10))
    print(display_text)
    AUTO = 1 - AUTO

def update_delays(key):
    global TURN_DELAY, GAME_DELAY

    if key == pygame.K_UP:
        TURN_DELAY = min(TURN_DELAY + 50, 1000)
        print("Turn delay: " + str(TURN_DELAY) + "ms")
    if key == pygame.K_DOWN:
        TURN_DELAY = max(TURN_DELAY - 50, 0)
        print("Turn delay: " + str(TURN_DELAY) + "ms")
    if key == pygame.K_RIGHT:
        GAME_DELAY = min(GAME_DELAY + 100, 5000)
        print("Game delay: " + str(GAME_DELAY) + "ms")
    if key == pygame.K_LEFT:
        GAME_DELAY = max(GAME_DELAY - 100, 0)
        print("Game delay: " + str(GAME_DELAY) + "ms")

def reset_round():
    new_board = create_board()
    pygame.draw.rect(screen, BLACK, (0, 0, width, height))
    draw_board(new_board)
    return new_board, False, "", random.randint(PLAYER, AI)

#region Initialize game
board = create_board()
game_over = False
move_sequence = ""

pygame.init()

SQUARESIZE = 100

width = COLUMN_COUNT * SQUARESIZE
height = (ROW_COUNT+1) * SQUARESIZE

size = (width, height)

RADIUS = int(SQUARESIZE/2 - 5)

screen = pygame.display.set_mode(size)
draw_board(board)
pygame.display.update()

turn = AI

game_font = pygame.font.SysFont("monospace", 75)
#endregion

turn = 0

#Game loop
while not game_over:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            sys.exit()

        if event.type == pygame.MOUSEMOTION:
            pygame.draw.rect(screen, BLACK, (0, 0, width, SQUARESIZE))
            posx = event.pos[0]
            if turn == PLAYER:
                pygame.draw.circle(screen, RED, (posx, int(SQUARESIZE/2)), RADIUS)

        pygame.display.update()

        if event.type == pygame.MOUSEBUTTONDOWN:
            pygame.draw.rect(screen, BLACK, (0, 0, width, SQUARESIZE))
            if turn == PLAYER:
                posx = event.pos[0]
                col = posx // SQUARESIZE

                if is_valid_location(board, col):
                    row = get_next_open_row(board, col)
                    animate_drop(board, col, row, PLAYER)
                    drop_piece(board, row, col, PLAYER)
                    move_sequence += str(col + 1)

                    win_line = winning_move(board, PLAYER)
                    if win_line:
                        write_score_to_file(PLAYER)
                        game_over = True
                        display_text = "Player 1 wins!!"
                        label = game_font.render(display_text, 1, BLUE)
                        print(display_text)
                        turn = random.randint(PLAYER, AI)
                        draw_board(board)
                        pygame.draw.line(screen, BLACK, win_line[0], win_line[1], 5)
                        screen.blit(label, (40,10))
                        pygame.display.update()
                        pygame.time.wait(GAME_DELAY)

                    draw_board(board)

                    turn += 1
                    turn = turn % 2

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                sys.exit()
            if event.key == pygame.K_SPACE:
                toggle_auto_mode()
            update_delays(event.key)


    if turn == AI and not game_over:               
        col = get_ai_move_from_solver(move_sequence, board)

        if col is not None and is_valid_location(board, col):
            row = get_next_open_row(board, col)
            pygame.time.wait(TURN_DELAY)
            animate_drop(board, col, row, AI)
            drop_piece(board, row, col, AI)
            move_sequence += str(col + 1)

            win_line = winning_move(board, AI)
            if win_line:
                display_text = "Player 2 wins!!"
                label = game_font.render(display_text, 1, BLUE)
                #print(display_text)
                draw_board(board)
                pygame.draw.line(screen, BLACK, win_line[0], win_line[1], 5)
                screen.blit(label, (40,10))
                pygame.display.update()
                pygame.time.wait(GAME_DELAY)
                write_score_to_file(AI)
                game_over = True
                turn = random.randint(PLAYER, AI)

            draw_board(board)

            turn += 1
            turn = turn % 2

    if not get_valid_locations(board):
        display_text = "It's a tie!"
        label = game_font.render(display_text, 1, BLUE)
        screen.blit(label, (40,10))
        pygame.display.update()
        pygame.time.wait(GAME_DELAY)
        write_score_to_file(TIE)
        board, game_over, move_sequence, turn = reset_round()

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
                        if event.key == pygame.K_KP_ENTER or event.key == pygame.K_RETURN:
                            board, game_over, move_sequence, turn = reset_round()
                            waiting = False
                        if event.key == pygame.K_SPACE:
                            toggle_auto_mode()
                            board, game_over, move_sequence, turn = reset_round()
                            waiting = False
                        update_delays(event.key)
            
