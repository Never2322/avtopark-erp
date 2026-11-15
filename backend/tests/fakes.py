# -*- coding: utf-8 -*-
"""Местные заглушки Telegram и почтового сервера: настоящие сервисы в тестах не вызываются, но запросы идут по сети, как в жизни."""
import base64
import email
import email.policy
import json
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "123456:TEST-token-ABC"


class FakeTelegram:
    """Отвечает как api.telegram.org: getMe, getUpdates, sendMessage, sendDocument. Можно подложить входящие и заставить ошибаться."""

    def __init__(self):
        self.sent: list[dict] = []          # {"chat_id", "text"} или {"chat_id", "file", "size", "caption"}
        self.updates: list[dict] = []
        self.requests: list[str] = []
        self.offsets: list[int] = []
        self.fail: dict[str, tuple[int, str]] = {}      # метод или chat_id → (код, описание)
        self.fail_times = 0                              # сколько первых отправок вернут ошибку (для проверки повторов)
        self.sleep_down = False
        self._next_update = 100
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, body):
                raw = json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                ctype = self.headers.get("Content-Type", "")
                path = self.path
                outer.requests.append(path)
                if not path.startswith(f"/bot{TOKEN}/"):
                    return self.reply(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
                method = path.split("/")[-1]
                if ctype.startswith("multipart/"):
                    msg = email.message_from_bytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + raw, policy=email.policy.default)
                    fields, file = {}, None
                    for part in msg.iter_parts():
                        name = part.get_param("name", header="content-disposition")
                        if part.get_filename():
                            file = (part.get_filename(), part.get_payload(decode=True))
                        else:
                            fields[name] = part.get_payload(decode=True).decode("utf-8")
                    data = fields
                else:
                    data, file = (json.loads(raw.decode("utf-8")) if raw else {}), None
                if method in outer.fail:
                    code, desc = outer.fail[method]
                    return self.reply(code, {"ok": False, "error_code": code, "description": desc})
                chat = str(data.get("chat_id", ""))
                if chat in outer.fail:
                    code, desc = outer.fail[chat]
                    return self.reply(code, {"ok": False, "error_code": code, "description": desc})
                if method in ("sendMessage", "sendDocument") and outer.fail_times > 0:
                    outer.fail_times -= 1
                    return self.reply(502, {"ok": False, "error_code": 502, "description": "Bad Gateway"})
                if method == "getMe":
                    return self.reply(200, {"ok": True, "result": {"id": 1, "is_bot": True, "username": "avtopark_test_bot"}})
                if method == "getUpdates":
                    offset = int(data.get("offset") or 0)
                    outer.offsets.append(offset)
                    outer.updates = [u for u in outer.updates if u["update_id"] >= offset]
                    return self.reply(200, {"ok": True, "result": list(outer.updates)})
                if method == "sendMessage":
                    outer.sent.append({"chat_id": chat, "text": data.get("text", "")})
                    return self.reply(200, {"ok": True, "result": {"message_id": len(outer.sent)}})
                if method == "sendDocument":
                    outer.sent.append({"chat_id": chat, "file": file[0], "size": len(file[1]), "bytes": file[1], "caption": data.get("caption", "")})
                    return self.reply(200, {"ok": True, "result": {"message_id": len(outer.sent)}})
                return self.reply(404, {"ok": False, "error_code": 404, "description": "Not Found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def say(self, chat_id: int, text: str, kind: str = "private", name: str = "Иван"):
        """Человек пишет боту."""
        self._next_update += 1
        self.updates.append({"update_id": self._next_update, "message": {"message_id": self._next_update, "text": text,
                                                                         "chat": {"id": chat_id, "type": kind}, "from": {"id": chat_id, "first_name": name}}})

    def texts(self, chat_id: int | str) -> list[str]:
        return [m["text"] for m in self.sent if m["chat_id"] == str(chat_id) and "text" in m]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class FakeSMTP:
    """Минимальный почтовый сервер: EHLO, AUTH PLAIN, MAIL, RCPT, DATA. Хранит принятые письма."""

    def __init__(self, user: str = "", password: str = ""):
        self.messages: list[dict] = []
        self.user, self.password = user, password
        self.reject = set()              # адреса, которые сервер отвергает (550)
        self.defer_times = 0             # первые N писем отвечают 451 (временная неудача)
        self.auth_seen: list[str] = []
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def send(self, line):
                self.wfile.write(line.encode("utf-8") + b"\r\n")
                self.wfile.flush()

            def handle(self):
                self.send("220 fake ESMTP")
                sender, rcpts, authed = "", [], not outer.user
                while True:
                    line = self.rfile.readline()
                    if not line:
                        return
                    cmd = line.decode("utf-8", "replace").strip()
                    up = cmd.upper()
                    if up.startswith("EHLO"):
                        self.wfile.write(b"250-fake\r\n250-8BITMIME\r\n" + (b"250-AUTH PLAIN\r\n" if outer.user else b"") + b"250 SIZE 52428800\r\n")
                        self.wfile.flush()
                    elif up.startswith("HELO"):
                        self.send("250 fake")
                    elif up.startswith("AUTH PLAIN"):
                        _, _, tail = cmd.partition("PLAIN ")
                        parts = base64.b64decode(tail).split(b"\0")
                        outer.auth_seen.append(parts[1].decode())
                        if parts[1].decode() == outer.user and parts[2].decode() == outer.password:
                            authed = True
                            self.send("235 ok")
                        else:
                            self.send("535 5.7.8 bad credentials")
                    elif up.startswith("MAIL FROM"):
                        if not authed:
                            self.send("530 5.7.0 Authentication required")
                            continue
                        sender, rcpts = cmd.split(":", 1)[1].strip().strip("<>").split(">")[0], []
                        self.send("250 ok")
                    elif up.startswith("RCPT TO"):
                        addr = cmd.split(":", 1)[1].strip().strip("<>").split(">")[0]
                        if addr in outer.reject:
                            self.send("550 5.1.1 no such user")
                        else:
                            rcpts.append(addr)
                            self.send("250 ok")
                    elif up == "DATA":
                        if outer.defer_times > 0:
                            outer.defer_times -= 1
                            self.send("451 4.3.0 try later")
                            continue
                        self.send("354 go")
                        data = bytearray()
                        while True:
                            chunk = self.rfile.readline()
                            if chunk in (b".\r\n", b""):
                                break
                            data += chunk[1:] if chunk.startswith(b"..") else chunk
                        outer.messages.append({"from": sender, "to": list(rcpts), "msg": email.message_from_bytes(bytes(data), policy=email.policy.default)})
                        self.send("250 queued")
                    elif up == "RSET":
                        self.send("250 ok")
                    elif up == "QUIT":
                        self.send("221 bye")
                        return
                    else:
                        self.send("502 unknown")

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
