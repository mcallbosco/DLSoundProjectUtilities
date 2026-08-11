"""Seed and launch the isolated local CDN and website preview."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class PreviewProcesses:
    worker: subprocess.Popen[str]
    website: subprocess.Popen[str]

    def stop(self) -> None:
        for process in (self.website, self.worker):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()


def _hidden_process_options() -> dict[str, object]:
    if sys.platform != "win32":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


def _executable(name: str) -> str:
    resolved = shutil.which(name)
    if not resolved:
        raise RuntimeError(f"Required executable is not available on PATH: {name}")
    return resolved


def seed_preview(
    worker_dir: Path,
    preview_root: Path,
    progress: Callable[[str], None] = print,
    *,
    reset: bool = True,
    suffix: str | None = None,
) -> None:
    script = worker_dir / "scripts" / "seed-local.mjs"
    if not script.is_file():
        raise RuntimeError(f"Preview seed script does not exist: {script}")
    state = worker_dir / ".wrangler" / "preview-state"
    command = [
        _executable("node"), str(script), "--source", str(preview_root),
        "--persist-to", str(state),
    ]
    if reset:
        command.append("--reset")
    if suffix:
        command.extend(("--suffix", suffix))
    progress("Seeding isolated local R2 preview state...")
    process = subprocess.Popen(
        command,
        cwd=worker_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_hidden_process_options(),
    )
    assert process.stdout is not None
    for line in process.stdout:
        progress(line.rstrip())
    exit_code = process.wait()
    if exit_code:
        raise RuntimeError(f"Local R2 seeding exited with status {exit_code}.")


def start_preview(
    worker_dir: Path,
    website_dir: Path,
    game: str,
    progress: Callable[[str], None] = print,
) -> PreviewProcesses:
    package = worker_dir / "package.json"
    website_package = website_dir / "package.json"
    if not package.is_file():
        raise RuntimeError(f"Worker package.json does not exist: {package}")
    if not website_package.is_file():
        raise RuntimeError(f"Website package.json does not exist: {website_package}")
    wrangler = worker_dir / "node_modules" / "wrangler" / "bin" / "wrangler.js"
    next_cli = website_dir / "node_modules" / "next" / "dist" / "bin" / "next"
    if not wrangler.is_file():
        raise RuntimeError(f"Wrangler is not installed. Run npm install in {worker_dir}")
    if not next_cli.is_file():
        raise RuntimeError(f"Next.js is not installed. Run npm install in {website_dir}")
    environment = os.environ.copy()
    environment["VLVIEWER_GAME"] = game
    environment["NEXT_PUBLIC_VLVIEWER_GAME"] = game
    environment["NEXT_PUBLIC_VLVIEWER_CONTENT_BASE_URL"] = "http://127.0.0.1:8787"
    prepare_game = website_dir / "scripts" / "prepare-game-config.mjs"
    if prepare_game.is_file():
        progress(f"Preparing the {game} website configuration in {website_dir}...")
        completed = subprocess.run(
            [_executable("node"), str(prepare_game)],
            cwd=website_dir,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **_hidden_process_options(),
        )
        if completed.stdout.strip():
            for line in completed.stdout.splitlines():
                progress(line.rstrip())
        if completed.returncode:
            raise RuntimeError(
                f"Website game configuration exited with status {completed.returncode}."
            )
    progress("Starting local content Worker at http://127.0.0.1:8787...")
    worker = subprocess.Popen(
        [
            _executable("node"), str(wrangler), "dev", "--local",
            "--persist-to", str(worker_dir / ".wrangler" / "preview-state"),
        ], cwd=worker_dir,
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True,
        **_hidden_process_options(),
    )
    progress("Starting the website with the local content origin...")
    website = subprocess.Popen(
        [_executable("node"), str(next_cli), "dev", "--turbopack"], cwd=website_dir,
        env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        text=True, **_hidden_process_options(),
    )
    return PreviewProcesses(worker=worker, website=website)


def restart_preview_worker(
    processes: PreviewProcesses,
    worker_dir: Path,
    progress: Callable[[str], None] = print,
) -> None:
    if processes.worker.poll() is None:
        processes.worker.terminate()
        try:
            processes.worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            processes.worker.kill()
    progress("Restarting local content Worker...")
    wrangler = worker_dir / "node_modules" / "wrangler" / "bin" / "wrangler.js"
    if not wrangler.is_file():
        raise RuntimeError(f"Wrangler is not installed. Run npm install in {worker_dir}")
    processes.worker = subprocess.Popen(
        [
            _executable("node"), str(wrangler), "dev", "--local",
            "--persist-to", str(worker_dir / ".wrangler" / "preview-state"),
        ], cwd=worker_dir,
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True,
        **_hidden_process_options(),
    )
