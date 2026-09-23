"""Bounded spawn workers: one fresh process, Agent and environment per game."""

from __future__ import annotations

import multiprocessing as mp
import os
import signal
import sys
import time
from dataclasses import dataclass
from multiprocessing.connection import wait
from pathlib import Path
from typing import Any, Callable, Sequence

from sc2bench_env.benchmark.evaluator import Evaluator
from sc2bench_env.interface.config import EpisodeConfig


def serialize_factories(backend_factory: Callable, agent_factory: Callable) -> bytes:
    # Optional and lazy: serial/core-only installations need neither dependency.
    try:
        import cloudpickle
        import psutil  # noqa: F401 -- validate cleanup support before launching
    except ImportError:
        raise ImportError("Parallel runs require: pip install 'sc2bench-env[parallel]'") from None
    try:
        return cloudpickle.dumps((backend_factory, agent_factory))
    except Exception as error:
        raise ValueError(
            f"Parallel factories must be serializable ({type(error).__name__}); "
            "create clients, locks and environments inside the factories"
        ) from None


def _failure(config: EpisodeConfig, error_type: str) -> dict[str, Any]:
    return {
        "episode_id": None, "config": config.to_dict(), "status": "failed",
        "outcome": "unfinished", "result": None, "end_reason": "worker_error",
        "game_time_seconds": None, "wall_time_seconds": None,
        "decision_count": None, "rejected_count": None,
        "record_directory": None, "error_type": error_type,
    }


def _worker_main(payload: bytes, config: EpisodeConfig, record_dir: str,
                 max_decisions: int, connection: Any, cancel: Any) -> None:
    # Ctrl+C belongs to the coordinator. Workers receive cooperative cancellation;
    # unresponsive calls are stopped with their own child process tree.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    row = None
    try:
        import cloudpickle
        from sc2bench_env.benchmark.runner import BenchmarkRunner

        backend_factory, agent_factory = cloudpickle.loads(payload)
        runner = BenchmarkRunner(backend_factory=backend_factory, record_dir=record_dir)
        row = runner._run_one(
            config, agent_factory, max_decisions, cancel_event=cancel,
            record_callback=lambda path: connection.send(("record", str(path))),
        )
    except BaseException as error:
        row = _failure(config, type(error).__name__)
    finally:
        # BurnySC2's cleanup registry is global. Spawn isolation makes this local
        # to this game, even if a backend thread did not finish closing promptly.
        sc2process = sys.modules.get("sc2.sc2process")
        if sc2process is not None:
            try:
                sc2process.kill_switch.kill_all()
            except Exception:
                pass
        try:
            connection.send(("result", row))
        except (BrokenPipeError, EOFError, OSError):
            pass
        connection.close()


@dataclass
class _Worker:
    index: int
    config: EpisodeConfig
    process: Any
    connection: Any
    cancel: Any
    record_directory: str | None = None
    result: dict[str, Any] | None = None
    eof: bool = False


def _stop_process_tree(process: Any) -> None:
    """Terminate only this owned worker and its descendants, never other games."""
    import psutil

    if not process.is_alive():
        process.join()
        return
    try:
        root = psutil.Process(process.pid)
        children = root.children(recursive=True)
        # Stop the worker first so it cannot launch more children during cleanup.
        root.suspend()
        for child in reversed(children):
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(children, timeout=2)
        for child in alive:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
    except psutil.NoSuchProcess:
        pass
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=3)
        if process.is_alive():
            process.kill()
            process.join(timeout=3)


def _read_messages(worker: _Worker) -> None:
    while not worker.eof:
        try:
            if not worker.connection.poll():
                break
            kind, value = worker.connection.recv()
        except (EOFError, OSError):
            worker.eof = True
            break
        if kind == "record":
            worker.record_directory = value
        elif kind == "result":
            worker.result = value


def _worker_result(worker: _Worker, error_type: str = "WorkerExited") -> dict[str, Any]:
    row = worker.result
    if row is None or (row.get("record_directory") is None and worker.record_directory is not None):
        error_type = row.get("error_type", error_type) if row is not None else error_type
        row = row or _failure(worker.config, error_type)
        if worker.record_directory is not None:
            try:
                # An abruptly stopped game's unfinished artifact stays incomplete;
                # do not invent a terminal result or overwrite its record.
                row = Evaluator.evaluate_episode(worker.record_directory)
                row["error_type"] = error_type
            except (OSError, ValueError, TypeError, KeyError):
                pass
            row["record_directory"] = worker.record_directory
    row["worker_pid"] = worker.process.pid
    row["worker_exitcode"] = worker.process.exitcode
    return row


def run_parallel(configs: Sequence[EpisodeConfig], payload: bytes, record_dir: Path,
                 max_decisions: int, max_parallel: int,
                 on_result: Callable[[int, dict[str, Any]], None]) -> None:
    """Refill freed slots immediately; the coordinator collects results in memory."""
    context = mp.get_context("spawn")  # consistent on Windows and Linux
    active: dict[int, _Worker] = {}
    next_index = 0
    try:
        while active or next_index < len(configs):
            while len(active) < max_parallel and next_index < len(configs):
                index = next_index + 1
                config = configs[next_index]
                next_index += 1
                receive, send = context.Pipe(duplex=False)
                cancel = context.Event()
                process = context.Process(
                    target=_worker_main,
                    args=(payload, config, str(record_dir), max_decisions, send, cancel),
                    name=f"sc2bench-episode-{index}",
                )
                try:
                    process.start()
                except Exception as error:
                    receive.close()
                    process.close()
                    on_result(index, _failure(config, type(error).__name__))
                else:
                    active[index] = _Worker(index, config, process, receive, cancel)
                finally:
                    send.close()
            if not active:
                continue
            handles = [worker.process.sentinel for worker in active.values()]
            handles += [worker.connection for worker in active.values() if not worker.eof]
            # Windows WaitForMultipleObjects has a handle limit. Every worker is
            # still polled below, so batching the wake-up set does not cap games.
            wait(handles[:60] if os.name == "nt" else handles, timeout=0.2)
            for index, worker in list(active.items()):
                _read_messages(worker)
                if worker.result is None and worker.process.is_alive():
                    continue
                worker.process.join(timeout=3)
                if worker.process.is_alive():
                    _stop_process_tree(worker.process)
                _read_messages(worker)
                row = _worker_result(worker)
                worker.connection.close()
                worker.process.close()
                del active[index]
                on_result(index, row)
    finally:
        # Do not start pending games after interruption/coordinator error.
        for worker in active.values():
            worker.cancel.set()
        deadline = time.monotonic() + 3
        while any(worker.process.is_alive() for worker in active.values()) and time.monotonic() < deadline:
            for worker in active.values():
                _read_messages(worker)
            time.sleep(0.05)
        results = []
        for worker in active.values():
            _stop_process_tree(worker.process)
            _read_messages(worker)
            row = _worker_result(worker, "WorkerInterrupted")
            worker.connection.close()
            worker.process.close()
            results.append((worker.index, row))
        # A failed index write must not leave the remaining workers running.
        for index, row in results:
            on_result(index, row)
