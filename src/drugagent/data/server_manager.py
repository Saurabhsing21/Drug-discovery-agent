from __future__ import annotations

import asyncio
import os
import subprocess
import time
from typing import Any

from drugagent.observability.health import run_source_health_checks
from drugagent.core.schema import SourceName
from drugagent.observability.telemetry import log_event


class ExternalServerContext:
    """Manages the lifecycle of external MCP background processes during execution."""

    def __init__(self, sources: list[SourceName]) -> None:
        self.sources = sources
        self._pharos_proc: subprocess.Popen[Any] | None = None
        self._log_file: Any | None = None

    async def __aenter__(self) -> "ExternalServerContext":
        for result in run_source_health_checks(type("Req", (), {"sources": self.sources})()):
            log_event("source_health_check", source=result.source, healthy=result.healthy, message=result.message)
        if SourceName.EXT_PHAROS in self.sources:
            await self._start_ext_pharos()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._pharos_proc:
            self._stop_pharos()

    async def _is_port_in_use(self, port: int) -> bool:
        # Simple check using netstat or lsof, here we just blindly try to connect
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return True
        except OSError:
            return False

    async def _start_ext_pharos(self) -> None:
        # Avoid starting if something is already on 8787 (like a manually started server)
        in_use = await self._is_port_in_use(8787)
        if in_use:
            log_event("external_server_port_in_use", source=SourceName.EXT_PHAROS.value, port=8787)
            return

        script_path = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "external_mcps", "run_pharos_mcp.sh"
        ))
        if not os.path.exists(script_path):
            raise RuntimeError(f"Pharos launcher missing at {script_path}")
        log_event("external_server_start", source=SourceName.EXT_PHAROS.value, command=script_path)

        self._log_file = open("pharos_server.log", "w")
        self._pharos_proc = subprocess.Popen(
            [script_path],
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,  # Run in distinct process group
        )

        # Poll the health endpoint until it responds
        import urllib.request
        from urllib.error import URLError

        timeout_s = float(os.getenv("A4T_PHAROS_START_TIMEOUT_S", "30"))
        start = time.time()
        ready = False
        while time.time() - start < timeout_s:
            try:
                # The SSE path itself will 400 or 404 without correct headers, but we just want to know it connects
                urllib.request.urlopen("http://127.0.0.1:8787/")
                ready = True
                break
            except URLError as e:
                # If it's an HTTPError (e.g. 404), the server is alive
                if hasattr(e, 'code'):
                    ready = True
                    break
                # If it's a connection refused, sleep and retry
                await asyncio.sleep(0.5)

        if ready:
            log_event("external_server_ready", source=SourceName.EXT_PHAROS.value, port=8787)
        else:
            self._stop_pharos()
            raise RuntimeError("Pharos server failed to report ready within timeout.")

    def _stop_pharos(self) -> None:
        if self._pharos_proc:
            import signal
            log_event("external_server_stop", source=SourceName.EXT_PHAROS.value)
            try:
                os.killpg(os.getpgid(self._pharos_proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            self._pharos_proc = None
            if hasattr(self, "_log_file") and self._log_file:
                self._log_file.close()
                self._log_file = None
