import json
import queue
import select
import sys
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Callable, Optional

from playwright.sync_api import Error as PlaywrightError

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.patch_stdout import patch_stdout
except Exception:
    PromptSession = None

    def patch_stdout(*args, **kwargs):
        return nullcontext()


EMOTE_ALIASES = {
    "scream": "1f631",
    "sunglasses": "1f60e",
    "smirk": "1f60f",
    "cry": "1f622",
    "sob": "1f62d",
    "wave": "1f44b",
    "thumbsup": "1f44d",
    "wink": "1f61c",
    "tongue": "1f61b",
    "sleep": "1f634",
    "zipper": "1f910",
    "grin": "1f601",
}


def normalize_emote_code(raw: str) -> Optional[str]:
    key = (raw or "").strip().lower()
    if not key:
        return None

    alias = EMOTE_ALIASES.get(key)
    if alias is not None:
        return alias

    if all(ch in "0123456789abcdef" for ch in key) and 4 <= len(key) <= 8:
        return key

    return None


class ParseDebugLogger:
    """Throttled parse diagnostics used on anomaly/recovery paths."""

    def __init__(self, enabled: bool, page):
        self.enabled = enabled
        self.page = page
        self._last_event_log: dict[str, float] = {}

    def log_event(
        self,
        event: str,
        payload: Optional[dict] = None,
        min_interval_sec: float = 2.0,
    ) -> None:
        if not self.enabled:
            return
        now = time.time()
        last = self._last_event_log.get(event, 0.0)
        if now - last < min_interval_sec:
            return
        self._last_event_log[event] = now

        body: dict = {"event": event}
        if isinstance(payload, dict):
            body["payload"] = payload
        print(f"[bridge][debug] {json.dumps(body, sort_keys=True)}")

    def log_parse_snapshot(
        self,
        event: str,
        payload: Optional[dict] = None,
        min_interval_sec: float = 2.0,
    ) -> None:
        if not self.enabled:
            return
        snap_payload = dict(payload) if isinstance(payload, dict) else {}
        try:
            snap_payload["snapshot"] = self.page.evaluate(
                """
                () => {
                  const raw = (window.__c4Bridge && window.__c4Bridge.readMoveSequenceDetailed)
                    ? window.__c4Bridge.readMoveSequenceDetailed()
                    : null;
                  const counts = (window.__c4Bridge && window.__c4Bridge.readGridColumnCounts)
                    ? window.__c4Bridge.readGridColumnCounts()
                    : null;
                  return { raw, counts };
                }
                """
            )
        except PlaywrightError as exc:
            snap_payload["snapshot_error"] = str(exc)
        self.log_event(event, snap_payload, min_interval_sec=min_interval_sec)


def start_operator_console(
    *,
    enabled: bool,
    operator_console_stop: threading.Event,
    operator_cmd_queue: queue.Queue[str],
) -> bool:
    if not enabled:
        return False
    if PromptSession is None:
        return False
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return False

    def operator_prompt_loop() -> None:
        session = PromptSession("[bridge cmd] ")
        with patch_stdout():
            while not operator_console_stop.is_set():
                try:
                    raw = session.prompt()
                except KeyboardInterrupt:
                    operator_cmd_queue.put("quit")
                    break
                except EOFError:
                    break
                if raw is None:
                    continue
                cmd = raw.strip()
                if not cmd:
                    continue
                operator_cmd_queue.put(cmd)

    t = threading.Thread(target=operator_prompt_loop, daemon=True)
    t.start()
    return True


def read_stdin_command(*, operator_console_started: bool) -> Optional[str]:
    # When prompt_toolkit owns stdin, avoid racing direct readline polling.
    if operator_console_started:
        return None

    try:
        ready, _, _ = select.select([sys.stdin], [], [], 0.0)
    except Exception:
        return None

    if not ready:
        return None

    raw = sys.stdin.readline()
    if raw is None:
        return None
    return raw


@dataclass
class OperatorCommandResult:
    should_exit: bool = False
    auto_control_state: Optional[str] = None
    post_game_wait_sec_runtime: Optional[float] = None
    delay_scale_runtime: Optional[float] = None
    trigger_matchmaking_start: bool = False
    quit_confirmation_pending: Optional[bool] = None


def process_operator_command(
    cmd: str,
    *,
    auto_control_state: str,
    post_game_wait_sec_runtime: float,
    delay_scale_runtime: float,
    post_game_mode: bool,
    match_active: bool,
    seeking_new_match: bool,
    site_mode: str,
    last_observed_url: str,
    quit_confirmation_pending: bool,
    auto_runtime_limit_sec: Optional[float],
    auto_runtime_hard_limit_sec: Optional[float],
    auto_runtime_start_at: float,
    default_emote_code: str,
    normalize_emote_code_fn: Callable[[str], Optional[str]],
    click_emoji_by_code_fn: Callable[[str], bool],
    clear_terminal_fn: Callable[[], None],
    print_board_fn: Callable[[], None],
    print_info_fn: Callable[[], None],
    set_auto_control_paused_fn: Callable[[str], None],
    is_live_room_url_fn: Callable[[str], bool],
    emote_aliases: dict[str, str],
    allow_matchmaking_start: bool,
) -> OperatorCommandResult:
    """Process one runtime operator command and return state updates."""
    result = OperatorCommandResult()
    cmd = cmd.strip().lower()
    if not cmd:
        return result

    if quit_confirmation_pending:
        if cmd == "y":
            print("[bridge] Quit requested by operator", flush=True)
            result.should_exit = True
            result.quit_confirmation_pending = False
            return result

        print("[bridge] Quit canceled", flush=True)
        result.quit_confirmation_pending = False

    if cmd in {"quit", "q", "exit"}:
        in_live_room = site_mode == "papergames" and is_live_room_url_fn(last_observed_url)
        in_live_game_session = (
            match_active
            and not post_game_mode
            and auto_control_state in {"running", "draining"}
        )
        if site_mode == "papergames":
            in_live_game_session = in_live_game_session and in_live_room

        if in_live_game_session:
            print(
                "[bridge] You are about to quit during a live game session, "
                "results are not guaranteed to be recorded. "
                "Do you want to continue? y/N",
                flush=True,
            )
            result.quit_confirmation_pending = True
            return result

        print("[bridge] Quit requested by operator", flush=True)
        result.should_exit = True
        return result

    if cmd in {"help", "h", "?"}:
        print(
            "[bridge] Commands: pause | resume | start(auto only) | status | wait <sec> | delay [x] | "
            "emote [code] | board | clear | info | quit"
        )
        print("[bridge] Emote examples: emote help | emote scream | emote sunglasses | emote 1f631")
        print("[bridge] Delay examples: delay | delay 0 | delay 0.95 | delay 1.10")
        return result

    if cmd in {"clear", "cls"}:
        clear_terminal_fn()
        return result

    if cmd in {"info", "i"}:
        print_info_fn()
        return result

    if cmd in {"board", "b"}:
        print_board_fn()
        return result

    parts = cmd.split()
    if parts and parts[0] == "emote":
        if len(parts) == 2 and parts[1] in {"help", "h", "?", "list"}:
            print("[bridge] Emote command help")
            print("[bridge] Usage: emote [alias_or_hex]")
            print(f"[bridge] Default (used by plain 'emote'): {default_emote_code}")
            print("[bridge] Tip: use 'emote <alias_or_hex>' to send a specific emote")
            return result

        if len(parts) > 2:
            print("[bridge] Usage: emote [alias_or_hex]")
            return result

        raw_emote = default_emote_code if len(parts) == 1 else parts[1]
        emote_code = normalize_emote_code_fn(raw_emote)
        if emote_code is None:
            aliases = ", ".join(sorted(emote_aliases.keys()))
            print(
                "[bridge] Invalid emote value; expected hex like 1f60e "
                f"or alias: {aliases}"
            )
            return result

        if click_emoji_by_code_fn(emote_code):
            print(f"[bridge] Emote sent ({emote_code})")
        else:
            print(
                f"[bridge] Emote send failed ({emote_code}); "
                "could not open emoji menu or find that emoji button"
            )
        return result

    if parts and parts[0] in {"wait", "postwait", "post-game-wait"}:
        if len(parts) == 1:
            print(f"[bridge] Current post-game wait: {post_game_wait_sec_runtime:.1f}s")
            return result
        if len(parts) != 2:
            print("[bridge] Usage: wait <seconds>")
            return result
        try:
            new_wait = float(parts[1])
        except ValueError:
            print("[bridge] wait expects a number, e.g. 'wait 6.5'")
            return result
        if new_wait < 0:
            print("[bridge] wait must be >= 0")
            return result
        result.post_game_wait_sec_runtime = new_wait
        print(f"[bridge] Updated post-game wait to {new_wait:.1f}s")
        return result

    if parts and parts[0] in {"delay", "pace", "tempo"}:
        if len(parts) == 1:
            print(f"[bridge] Current auto delay scale: x{delay_scale_runtime:.2f}")
            return result
        if len(parts) != 2:
            print("[bridge] Usage: delay <scale>")
            return result
        try:
            new_scale = float(parts[1])
        except ValueError:
            print("[bridge] delay expects a number, e.g. 'delay 0.95'")
            return result
        if new_scale < 0.0 or new_scale > 1.8:
            print("[bridge] delay scale must be between 0.00 and 1.80")
            return result
        result.delay_scale_runtime = new_scale
        print(f"[bridge] Updated auto delay scale to x{new_scale:.2f}")
        return result

    if cmd in {"status", "s"}:
        in_live_room = site_mode == "papergames" and is_live_room_url_fn(last_observed_url)
        runtime_info = ""
        if auto_runtime_limit_sec is not None:
            elapsed = max(0.0, time.time() - auto_runtime_start_at)
            remaining = max(0.0, auto_runtime_limit_sec - elapsed)
            hard_remaining = (
                max(0.0, auto_runtime_hard_limit_sec - elapsed)
                if auto_runtime_hard_limit_sec is not None
                else 0.0
            )
            runtime_info = (
                f" auto_runtime_limit_sec={auto_runtime_limit_sec:.1f}"
                f" auto_runtime_remaining_sec={remaining:.1f}"
                f" auto_runtime_hard_remaining_sec={hard_remaining:.1f}"
            )
        print(
            "[bridge] Control status: "
            f"state={auto_control_state} match_active={match_active} "
            f"post_game_mode={post_game_mode} seeking_new_match={seeking_new_match} "
            f"live_room_url={in_live_room} "
            f"post_game_wait_sec={post_game_wait_sec_runtime:.1f} "
            f"delay_scale=x{delay_scale_runtime:.2f}"
            f"{runtime_info}"
        )
        return result

    if cmd in {"pause", "p", "stop"}:
        if auto_control_state == "paused":
            print("[bridge] Already paused")
            return result
        in_live_room = site_mode == "papergames" and is_live_room_url_fn(last_observed_url)
        if post_game_mode:
            set_auto_control_paused_fn("paused immediately")
            result.auto_control_state = "paused"
            return result
        if not match_active and not in_live_room:
            set_auto_control_paused_fn("paused immediately")
            result.auto_control_state = "paused"
            return result

        result.auto_control_state = "draining"
        print("[bridge] Drain requested: will pause after current game resolves")
        return result

    if cmd in {"resume", "r", "run"}:
        if auto_control_state == "running":
            print("[bridge] Already running")
            return result
        result.auto_control_state = "running"
        print("[bridge] Auto resumed")
        return result

    if cmd in {"start", "queue", "match", "go"}:
        if not allow_matchmaking_start:
            print("[bridge] Start/queue command is unavailable in standby mode")
            return result
        in_live_room = site_mode == "papergames" and is_live_room_url_fn(last_observed_url)
        if match_active and in_live_room and not post_game_mode:
            print("[bridge] Already in a live match")
            return result
        if seeking_new_match:
            print("[bridge] Matchmaking already requested")
            return result

        # start implies runtime should be actively driving queue controls.
        if auto_control_state != "running":
            result.auto_control_state = "running"

        result.trigger_matchmaking_start = True
        print("[bridge] Start requested: queueing a new match")
        return result

    print(
        f"[bridge] Unknown command: {cmd}. "
        "Try: pause | resume | start | status | wait <sec> | delay [x] | emote [code] | board | clear | info | quit"
    )
    return result


def handle_operator_command_stream(
    *,
    mode: str,
    operator_cmd_queue: queue.Queue[str],
    operator_console_started: bool,
    process_one_fn: Callable[[str], OperatorCommandResult],
) -> OperatorCommandResult:
    """Consume queued/stdin commands and return the last state update snapshot."""
    result = OperatorCommandResult()
    if mode not in {"auto", "standby"}:
        return result

    while True:
        try:
            queued_cmd = operator_cmd_queue.get_nowait()
        except queue.Empty:
            break

        cmd_result = process_one_fn(queued_cmd)
        if cmd_result.auto_control_state is not None:
            result.auto_control_state = cmd_result.auto_control_state
        if cmd_result.post_game_wait_sec_runtime is not None:
            result.post_game_wait_sec_runtime = cmd_result.post_game_wait_sec_runtime
        if cmd_result.delay_scale_runtime is not None:
            result.delay_scale_runtime = cmd_result.delay_scale_runtime
        if cmd_result.trigger_matchmaking_start:
            result.trigger_matchmaking_start = True
        if cmd_result.should_exit:
            result.should_exit = True
            return result

    raw = read_stdin_command(operator_console_started=operator_console_started)
    if raw is None:
        return result

    cmd_result = process_one_fn(raw)
    if cmd_result.auto_control_state is not None:
        result.auto_control_state = cmd_result.auto_control_state
    if cmd_result.post_game_wait_sec_runtime is not None:
        result.post_game_wait_sec_runtime = cmd_result.post_game_wait_sec_runtime
    if cmd_result.delay_scale_runtime is not None:
        result.delay_scale_runtime = cmd_result.delay_scale_runtime
    if cmd_result.trigger_matchmaking_start:
        result.trigger_matchmaking_start = True
    if cmd_result.should_exit:
        result.should_exit = True
    return result
