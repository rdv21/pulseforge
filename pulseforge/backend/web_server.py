"""PulseForge Web Server — HTTP + WebSocket control panel.

Runs in a daemon thread alongside the Qt UI. Serves static files,
HTTP API endpoints, and WebSocket real-time VU pushes.

No external dependencies — uses only stdlib (http.server, socket, struct,
hashlib, base64, json, threading, os, time).
"""
import json
import os
import struct
import socket
import threading
import time
import hashlib
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs


# ─── WebSocket protocol constants ─────────────────────────────────
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WS_OPCODE_TEXT = 0x1
WS_OPCODE_CLOSE = 0x8
WS_OPCODE_PING = 0x9
WS_OPCODE_PONG = 0xA


class WebSocketConnection:
    """Minimal WebSocket server-side connection (RFC 6455 text frames only)."""

    def __init__(self, request_socket):
        self._sock = request_socket
        self._closed = False
        self._send_lock = threading.Lock()

    def send_text(self, message: str):
        """Send a text frame to the client. Returns True on success."""
        if self._closed:
            return False
        try:
            data = message.encode("utf-8")
            frame = self._encode_frame(WS_OPCODE_TEXT, data)
            with self._send_lock:
                self._sock.sendall(frame)
            return True
        except (OSError, BrokenPipeError, ConnectionResetError):
            self._closed = True
            return False

    def recv_message(self, timeout: float = None) -> str | None:
        """Receive a single text message. Returns None on close/error."""
        if self._closed:
            return None
        if timeout is not None:
            self._sock.settimeout(timeout)
        try:
            return self._recv_frame()
        except (socket.timeout, OSError, ConnectionResetError):
            return None
        finally:
            self._sock.settimeout(None)

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            frame = self._encode_frame(WS_OPCODE_CLOSE, b"\x03\xe8")  # 1000 normal
            self._sock.sendall(frame)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    @property
    def is_closed(self) -> bool:
        return self._closed

    # ─── Frame encoding/decoding ───

    def _encode_frame(self, opcode: int, payload: bytes) -> bytes:
        """Encode a WebSocket frame (server→client, no mask)."""
        b0 = 0x80 | opcode  # FIN bit set
        length = len(payload)
        header = bytearray([b0])
        if length <= 125:
            header.append(length)
        elif length <= 0xFFFF:
            header.append(126)
            header.extend(struct.pack(">H", length))
        else:
            header.append(127)
            header.extend(struct.pack(">Q", length))
        return bytes(header) + payload

    def _recv_frame(self) -> str | None:
        """Read one frame from the socket. Handles text, ping, pong, close."""
        while True:
            b0 = self._recv_exact(1)
            if b0 is None:
                return None
            fin = b0[0] & 0x80
            opcode = b0[0] & 0x0F

            b1 = self._recv_exact(1)
            if b1 is None:
                return None
            masked = b1[0] & 0x80
            payload_len = b1[0] & 0x7F

            if payload_len == 126:
                ext = self._recv_exact(2)
                if ext is None:
                    return None
                payload_len = struct.unpack(">H", ext)[0]
            elif payload_len == 127:
                ext = self._recv_exact(8)
                if ext is None:
                    return None
                payload_len = struct.unpack(">Q", ext)[0]

            mask_key = b""
            if masked:
                mask_key = self._recv_exact(4)
                if mask_key is None:
                    return None

            payload = self._recv_exact(payload_len)
            if payload is None:
                return None

            if masked:
                payload = bytes(
                    payload[i] ^ mask_key[i % 4] for i in range(len(payload))
                )

            if opcode == WS_OPCODE_PING:
                pong = self._encode_frame(WS_OPCODE_PONG, payload)
                self._sock.sendall(pong)
                continue
            elif opcode == WS_OPCODE_PONG:
                continue
            elif opcode == WS_OPCODE_CLOSE:
                self._closed = True
                return None
            elif opcode == WS_OPCODE_TEXT:
                return payload.decode("utf-8")
            else:
                continue  # ignore unknown opcodes

    def _recv_exact(self, n: int) -> bytes | None:
        """Read exactly n bytes from the socket."""
        if n == 0:
            return b""
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self._sock.recv(n - len(buf))
            except (socket.timeout, OSError):
                return None
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)


class WebServer:
    """HTTP + WebSocket server for PulseForge web control panel.

    Runs in a daemon thread. Serves static files from web/ and provides
    HTTP API endpoints that bridge to the PulseForgeBridge methods.
    """

    def __init__(self, bridge, port: int = 8765, host: str = "0.0.0.0"):
        self._bridge = bridge
        self._port = port
        self._host = host
        self._running = False
        self._server: ThreadingHTTPServer | None = None
        self._ws_clients: set[WebSocketConnection] = set()
        self._ws_lock = threading.Lock()
        self._vu_thread: threading.Thread | None = None
        self._web_dir = Path(__file__).parent.parent / "web"

        # MIME types for static file serving
        self._mime_types = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".gif": "image/gif",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
            ".woff2": "font/woff2",
            ".woff": "font/woff",
            ".ttf": "font/ttf",
            ".vrmanifest": "application/json",
            ".map": "application/json",
        }

    def start(self):
        """Start the HTTP + WebSocket server in a daemon thread."""
        if self._running:
            return
        self._running = True
        handler = self._make_handler()
        try:
            self._server = ThreadingHTTPServer(
                (self._host, self._port), handler
            )
            self._server.daemon_threads = True
        except OSError as e:
            print(f"  WebServer: failed to bind {self._host}:{self._port}: {e}")
            self._running = False
            return

        self._server_thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._server_thread.start()

        # Start VU push thread
        self._vu_thread = threading.Thread(target=self._vu_loop, daemon=True)
        self._vu_thread.start()

        print(f"  WebServer: listening on http://{self._host}:{self._port}")

    def stop(self):
        """Stop the server."""
        self._running = False
        if self._server:
            self._server.shutdown()
        # Close all WebSocket clients
        with self._ws_lock:
            for client in list(self._ws_clients):
                client.close()
            self._ws_clients.clear()

    # ─── WebSocket client management ───

    def _add_client(self, client: WebSocketConnection):
        with self._ws_lock:
            self._ws_clients.add(client)

    def _remove_client(self, client: WebSocketConnection):
        with self._ws_lock:
            self._ws_clients.discard(client)

    def _broadcast(self, message: str):
        """Send a message to all connected WebSocket clients."""
        with self._ws_lock:
            dead = []
            for client in self._ws_clients:
                if not client.send_text(message):
                    dead.append(client)
            for client in dead:
                self._ws_clients.discard(client)

    # ─── VU push thread ───

    def _vu_loop(self):
        """Push VU data to all WebSocket clients at ~30fps."""
        while self._running:
            try:
                vu_data = self._collect_vu()
                msg = json.dumps({"type": "vu", "data": vu_data})
                self._broadcast(msg)
            except Exception:
                pass
            time.sleep(0.033)

    def _collect_vu(self) -> dict:
        """Collect current VU state from the bridge."""
        data = {"master": 0.0, "channels": {}, "mic": 0.0}
        if not self._bridge:
            return data

        try:
            # Master VU (gaming sink)
            gaming_id = self._bridge._group_sink_ids.get("gaming")
            if gaming_id is not None:
                monitors = self._bridge._vu_poller._monitors
                monitor = monitors.get(gaming_id)
                if monitor:
                    data["master"] = float(monitor.get_peak())

            # Per-channel VU
            for channel in ("game", "chat", "media", "aux"):
                node_id = self._bridge._group_sink_ids.get(channel)
                if node_id is not None:
                    monitors = self._bridge._vu_poller._monitors
                    monitor = monitors.get(node_id)
                    if monitor:
                        peak = float(monitor.get_peak())
                        group_cfg = self._bridge._config.get("groups", {}).get(channel, {})
                        main_vol = group_cfg.get("output_volume", 1.0)
                        stream_vol = group_cfg.get("stream_volume", 1.0)
                        stream_enabled = group_cfg.get("stream_enabled", False)
                        data["channels"][channel] = {
                            "main": peak * main_vol,
                            "stream": peak * stream_vol if stream_enabled else 0.0,
                        }

            # Mic VU
            data["mic"] = float(self._bridge._mic_chain.get_peak_vu())
        except Exception:
            pass

        return data

    # ─── HTTP handler factory ───

    def _make_handler(self):
        """Create a request handler class with closure over self."""
        server = self

        class Handler(BaseHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)

            def log_message(self, format, *args):
                pass  # suppress default logging

            # ─── GET handler ───

            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path
                params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

                # WebSocket upgrade
                if path == "/ws" and self.headers.get("Upgrade", "").lower() == "websocket":
                    self._handle_websocket()
                    return

                # ─── API endpoints ───
                if path.startswith("/api/"):
                    self._handle_api("GET", path, params, body=None)
                    return

                # ─── Static files ───
                if path == "/":
                    path = "/index.html"
                server._serve_static(self, path)

            # ─── POST handler ───

            def do_POST(self):
                parsed = urlparse(self.path)
                path = parsed.path
                params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

                # Read body
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length) if content_length > 0 else None

                if path.startswith("/api/"):
                    self._handle_api("POST", path, params, body)
                else:
                    self.send_error(404, "Not Found")

            # ─── WebSocket handler ───

            def _handle_websocket(self):
                key = self.headers.get("Sec-WebSocket-Key")
                if not key:
                    self.send_error(400, "Missing Sec-WebSocket-Key")
                    return

                # Compute accept value
                accept = base64.b64encode(
                    hashlib.sha1((key + WS_GUID).encode()).digest()
                ).decode()

                # Send 101 Switching Protocols
                response = (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n"
                    "\r\n"
                )
                self.wfile.write(response.encode())
                self.wfile.flush()

                # Create WebSocket connection on the raw socket
                ws_conn = WebSocketConnection(self.request)
                server._add_client(ws_conn)

                # Read loop — keep connection alive, handle incoming messages
                try:
                    while not ws_conn.is_closed:
                        msg = ws_conn.recv_message(timeout=1.0)
                        if msg is None:
                            break
                        # We don't process incoming messages (all commands go via HTTP)
                        # but we need to keep the connection alive
                except Exception:
                    pass
                finally:
                    server._remove_client(ws_conn)
                    ws_conn.close()

            # ─── API handler ───

            def _handle_api(self, method, path, params, body):
                bridge = server._bridge
                try:
                    result = server._route_api(method, path, params, body, bridge)
                    if result is not None:
                        self._send_json(200, result)
                    else:
                        self._send_json(204, {"status": "ok"})
                except Exception as e:
                    self._send_json(500, {"error": str(e)})

            def _send_json(self, status, data):
                body = json.dumps(data).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

        return Handler

    # ─── Static file serving ───

    def _serve_static(self, handler, path: str):
        """Serve a static file from the web directory."""
        # Normalize path — prevent directory traversal
        clean_path = path.lstrip("/")
        if ".." in clean_path:
            handler.send_error(403, "Forbidden")
            return

        file_path = self._web_dir / clean_path
        if not file_path.exists() or not file_path.is_file():
            handler.send_error(404, "Not Found")
            return

        ext = file_path.suffix.lower()
        mime = self._mime_types.get(ext, "application/octet-stream")

        try:
            with open(file_path, "rb") as f:
                content = f.read()
            handler.send_response(200)
            handler.send_header("Content-Type", mime)
            handler.send_header("Content-Length", str(len(content)))
            handler.send_header("Access-Control-Allow-Origin", "*")
            handler.end_headers()
            handler.wfile.write(content)
        except Exception:
            handler.send_error(500, "Internal Server Error")

    # ─── API routing ───

    def _route_api(self, method, path, params, body, bridge):
        """Route an API request to the appropriate bridge method.

        Returns a JSON-serializable result dict, or None for no-content.
        Raises Exception on errors.
        """
        # ─── Soundboard ───
        if path == "/api/soundboard/slots" and method == "GET":
            page = int(params.get("page", 0))
            return {"slots": bridge.getSoundboardSlots(page)}

        if path == "/api/soundboard/play" and method == "POST":
            page = int(params.get("page", 0))
            index = int(params.get("index", 0))
            bridge.playSound(page, index)
            return {"status": "ok"}

        if path == "/api/soundboard/playing" and method == "GET":
            page = int(params.get("page", 0))
            index = int(params.get("index", 0))
            return {"playing": bridge.isSoundPlaying(page, index)}

        if path == "/api/soundboard/clear" and method == "POST":
            page = int(params.get("page", 0))
            index = int(params.get("index", 0))
            bridge.clearSoundSlot(page, index)
            return {"status": "ok"}

        if path == "/api/soundboard/recording" and method == "GET":
            return {"enabled": bridge.isRecordingEnabled()}

        if path == "/api/soundboard/recording" and method == "POST":
            enabled = params.get("enabled", "true").lower() == "true"
            bridge.setRecordingEnabled(enabled)
            return {"status": "ok", "enabled": enabled}

        if path == "/api/soundboard/channels/recording" and method == "GET":
            return {"channels": bridge.getRecordingChannels()}

        if path == "/api/soundboard/channel/start" and method == "POST":
            channel = params.get("channel", "")
            bridge.startChannelRecording(channel)
            return {"status": "ok"}

        if path == "/api/soundboard/channel/stop" and method == "POST":
            channel = params.get("channel", "")
            bridge.stopChannelRecording(channel)
            return {"status": "ok"}

        if path == "/api/soundboard/channel/recording" and method == "GET":
            channel = params.get("channel", "")
            return {"recording": bridge.isChannelRecording(channel)}

        if path == "/api/soundboard/capture" and method == "POST":
            channel = params.get("channel", "")
            bridge.captureClip(channel)
            return {"status": "ok"}

        if path == "/api/soundboard/clip/info" and method == "GET":
            return bridge.getClipInfo()

        if path == "/api/soundboard/clip/waveform" and method == "GET":
            return {"peaks": bridge.getClipWaveform()}

        if path == "/api/soundboard/clip/trim" and method == "POST":
            start = float(params.get("start", 0))
            end = float(params.get("end", 0))
            bridge.setClipTrim(start, end)
            return {"status": "ok"}

        if path == "/api/soundboard/clip/play" and method == "POST":
            bridge.playClipPreview()
            return {"status": "ok"}

        if path == "/api/soundboard/clip/stop" and method == "POST":
            bridge.stopClipPreview()
            return {"status": "ok"}

        if path == "/api/soundboard/clip/playing" and method == "GET":
            return {"playing": bridge.isClipPlaying()}

        if path == "/api/soundboard/clip/clear" and method == "POST":
            bridge.clearClip()
            return {"status": "ok"}

        if path == "/api/soundboard/clip/publish/resolve" and method == "GET":
            name = params.get("name", "")
            return bridge.resolvePublishName(name)

        if path == "/api/soundboard/clip/publish" and method == "POST":
            name = params.get("name", "")
            overwrite = params.get("overwrite", "false").lower() == "true"
            path_result = bridge.publishClip(name, overwrite)
            result = {"path": path_result}
            if not path_result:
                result["conflict"] = bridge.getLastPublishConflict()
            return result

        if path == "/api/soundboard/clip/assign" and method == "POST":
            page = int(params.get("page", 0))
            index = int(params.get("index", 0))
            ok = bridge.assignPublishedClip(page, index)
            return {"assigned": ok}

        if path == "/api/soundboard/clip/clear-published" and method == "POST":
            bridge.clearPublished()
            return {"status": "ok"}

        if path == "/api/soundboard/clip/last" and method == "GET":
            return bridge.getLastPublished()

        if path == "/api/soundboard/output" and method == "GET":
            return {"target": bridge.getSoundboardOutput()}

        if path == "/api/soundboard/output" and method == "POST":
            target = params.get("target", "pulseforge_gaming")
            bridge.setSoundboardOutput(target)
            return {"status": "ok", "target": target}

        # ─── Devices (input / aux external input) ───
        if path == "/api/devices/aux-input" and method == "GET":
            return {"devices": bridge.getAuxInputDevices()}

        if path == "/api/devices/aux-input" and method == "POST":
            name = params.get("name", "")
            bridge.setAuxInput(name)
            return {"status": "ok", "name": name}

        # ─── Mixer ───
        if path == "/api/mixer/channels" and method == "GET":
            return {"channels": bridge.getChannelGroups()}

        if path == "/api/mixer/channel" and method == "GET":
            channel = params.get("channel", "")
            return bridge.getChannelState(channel)

        if path == "/api/mixer/volume" and method == "POST":
            channel = params.get("channel", "")
            volume = float(params.get("volume", 1.0))
            bridge.setChannelVolume(channel, volume)
            return {"status": "ok"}

        if path == "/api/mixer/stream-volume" and method == "POST":
            channel = params.get("channel", "")
            volume = float(params.get("volume", 1.0))
            bridge.setChannelStreamVolume(channel, volume)
            return {"status": "ok"}

        if path == "/api/mixer/stream" and method == "POST":
            channel = params.get("channel", "")
            enabled = params.get("enabled", "false").lower() == "true"
            bridge.setChannelStream(channel, enabled)
            return {"status": "ok"}

        if path == "/api/mixer/mute" and method == "POST":
            channel = params.get("channel", "")
            muted = params.get("muted", "false").lower() == "true"
            bridge.setChannelMute(channel, muted)
            return {"status": "ok"}

        # ─── Mic ───
        if path == "/api/mic/state" and method == "GET":
            return bridge.getMicState()

        if path == "/api/mic/settings" and method == "GET":
            return bridge.getMicSettings()

        if path == "/api/mic/volume" and method == "POST":
            volume = float(params.get("volume", 1.0))
            bridge.setMicVolume(volume)
            return {"status": "ok"}

        if path == "/api/mic/mute" and method == "POST":
            muted = params.get("muted", "false").lower() == "true"
            bridge.setMicMute(muted)
            return {"status": "ok"}

        if path == "/api/mic/monitor" and method == "POST":
            enabled = params.get("enabled", "false").lower() == "true"
            bridge.setMicMonitor(enabled)
            return {"status": "ok"}

        if path == "/api/mic/stream" and method == "POST":
            enabled = params.get("enabled", "false").lower() == "true"
            bridge.setMicStream(enabled)
            return {"status": "ok"}

        # ─── AFX ───
        if path == "/api/mic/afx/status" and method == "GET":
            return bridge.getAfxStatus()

        if path == "/api/mic/afx/enabled" and method == "POST":
            enabled = params.get("enabled", "false").lower() == "true"
            bridge.setAfxEnabled(enabled)
            return {"status": "ok"}

        if path == "/api/mic/afx/mode" and method == "POST":
            mode = params.get("mode", "denoiser")
            bridge.setAfxEffectMode(mode)
            return {"status": "ok"}

        if path == "/api/mic/afx/intensity" and method == "POST":
            intensity = float(params.get("intensity", 0.7))
            bridge.setAfxIntensity(intensity)
            return {"status": "ok"}

        # ─── Gate ───
        if path == "/api/mic/gate/enabled" and method == "POST":
            enabled = params.get("enabled", "false").lower() == "true"
            bridge.setGateEnabled(enabled)
            return {"status": "ok"}

        if path == "/api/mic/gate/threshold" and method == "POST":
            value = float(params.get("value", -50))
            bridge.setGateThreshold(value)
            return {"status": "ok"}

        if path == "/api/mic/gate/range" and method == "POST":
            value = float(params.get("value", -25))
            bridge.setGateRange(value)
            return {"status": "ok"}

        if path == "/api/mic/gate/attack" and method == "POST":
            value = float(params.get("value", 25))
            bridge.setGateAttack(value)
            return {"status": "ok"}

        if path == "/api/mic/gate/hold" and method == "POST":
            value = float(params.get("value", 300))
            bridge.setGateHold(value)
            return {"status": "ok"}

        if path == "/api/mic/gate/release" and method == "POST":
            value = float(params.get("value", 200))
            bridge.setGateRelease(value)
            return {"status": "ok"}

        # ─── Compressor ───
        if path == "/api/mic/comp/enabled" and method == "POST":
            enabled = params.get("enabled", "false").lower() == "true"
            bridge.setCompEnabled(enabled)
            return {"status": "ok"}

        if path == "/api/mic/comp/threshold" and method == "POST":
            value = float(params.get("value", -20))
            bridge.setCompThreshold(value)
            return {"status": "ok"}

        if path == "/api/mic/comp/ratio" and method == "POST":
            value = float(params.get("value", 3))
            bridge.setCompRatio(value)
            return {"status": "ok"}

        if path == "/api/mic/comp/makeup" and method == "POST":
            value = float(params.get("value", 0))
            bridge.setCompMakeup(value)
            return {"status": "ok"}

        # ─── EQ ───
        if path == "/api/mic/eq/enabled" and method == "POST":
            enabled = params.get("enabled", "false").lower() == "true"
            bridge.setEqEnabled(enabled)
            return {"status": "ok"}

        if path == "/api/mic/eq/band" and method == "POST":
            idx = int(params.get("idx", 0))
            freq = float(params.get("freq", 1000))
            gain = float(params.get("gain", 0))
            q = float(params.get("q", 1.0))
            bridge.setEqBand(idx, freq, gain, q)
            return {"status": "ok"}

        if path == "/api/mic/eq/bands" and method == "GET":
            return {"bands": bridge.getEqBands()}

        # ─── Misc ───
        if path == "/api/status" and method == "GET":
            return {"status": "running", "version": "0.1.0"}

        raise Exception(f"Unknown endpoint: {method} {path}")
