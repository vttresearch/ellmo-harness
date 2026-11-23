"""Launch the ELLMO Harness REST and WebSocket services."""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent


def local_speech_command():
    """Read an optional speech-service launch command from the active config."""
    config_path = Path(os.environ.get("ELLMO_CONFIG") or ROOT / "backend/config.json")
    if not config_path.expanduser().is_file():
        return None
    with config_path.expanduser().open(encoding="utf-8") as config_file:
        config = json.load(config_file)
    voice = config.get("voice_input") if isinstance(config, dict) else None
    if not isinstance(voice, dict) or voice.get("enabled") is not True:
        return None
    command = voice.get("launch_command")
    if command is not None and (
        not isinstance(command, list)
        or not command
        or not all(isinstance(arg, str) and arg.strip() for arg in command)
    ):
        raise ValueError("voice_input.launch_command must be a nonempty list of strings.")
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Use 0.0.0.0 for remote Unity clients")
    parser.add_argument("--rest-port", type=int, default=8000)
    parser.add_argument("--ws-port", type=int, default=8001)
    args = parser.parse_args()

    if sys.version_info[:2] != (3, 13):
        parser.error("Activate a Python 3.13 environment first.")
    if args.rest_port == args.ws_port or any(
        not 1 <= port <= 65535 for port in (args.rest_port, args.ws_port)
    ):
        parser.error("REST and WebSocket ports must be distinct numbers from 1 to 65535.")

    env = os.environ.copy()
    env.setdefault("HF_HOME", str(ROOT / ".cache" / "huggingface"))
    env.setdefault("XDG_CACHE_HOME", str(ROOT / ".cache"))
    env.setdefault("TORCH_HOME", str(ROOT / ".cache" / "torch"))
    env["PYTHONUNBUFFERED"] = "1"
    commands = [
        ("REST", ROOT / "backend", [sys.executable, "-m", "uvicorn", "api_rest:app", "--host", args.host, "--port", str(args.rest_port)]),
        ("WebSocket", ROOT / "backend", [sys.executable, "-m", "uvicorn", "api_ws:app", "--host", args.host, "--port", str(args.ws_port)]),
    ]
    try:
        speech_command = local_speech_command()
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if speech_command:
        commands.append(("Speech", ROOT, speech_command))
    children: list[subprocess.Popen] = []
    chat_services: list[subprocess.Popen] = []
    speech_process = None

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, interrupted)

    try:
        for name, cwd, command in commands:
            options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {}
            child_env = os.environ if name == "Speech" else env
            try:
                process = subprocess.Popen(command, cwd=cwd, env=child_env, **options)
            except OSError as exc:
                if name != "Speech":
                    raise
                print(f"Speech service could not start: {exc}", file=sys.stderr, flush=True)
                continue
            children.append(process)
            if name == "Speech":
                speech_process = process
            else:
                chat_services.append(process)
            print(f"{name}: PID {process.pid}, executable {command[0]}", flush=True)
        while True:
            if speech_process is not None and speech_process.poll() is not None:
                print(f"Speech service exited with code {speech_process.returncode}.", file=sys.stderr, flush=True)
                speech_process = None
            for child in chat_services:
                code = child.poll()
                if code is not None:
                    print(f"Service PID {child.pid} exited ({code}); stopping remaining services.", flush=True)
                    return code or 1
            time.sleep(0.25)
    except KeyboardInterrupt:
        return 0
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        for child in children:
            if child.poll() is None:
                try:
                    child.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
                except ProcessLookupError:
                    pass
        for child in children:
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], check=False)
                else:
                    child.kill()
                child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
