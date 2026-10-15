import os
import random
import re
import socket
import struct
import sys
import threading
import time
import uuid
import base64
import json
import zlib
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
import jwt as pyjwt

# ─────────────────────────────────────────────
# FastAPI Setup & Endpoints para la Web
# ─────────────────────────────────────────────
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permitido para tu GitHub Pages
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class BotConfig(BaseModel):
    host: str
    port: int
    name: str
    bots: int
    hold: int
    timeout: int
    coldown: float

recent_logs = []
global_stop_event = threading.Event()
active_bots_list = []

def api_log(msg: str):
    timestamp = datetime.now().strftime("[%H:%M:%S]")
    line = f"{timestamp} {msg}"
    print(line)
    recent_logs.append(line)
    if len(recent_logs) > 200:
        recent_logs.pop(0)

@app.get("/")
def root():
    return {"status": "online", "message": "Backend de ThenBlex (Motor Completo) activo"}

@app.get("/api/logs")
def get_logs():
    return {"logs": recent_logs}

@app.post("/api/stop")
def stop_all_bots():
    global_stop_event.set()
    for b in active_bots_list:
        try:
            b.send_disconnect()
        except Exception:
            pass
    api_log("[!] Orden de parada ejecutada para todos los bots.")
    return {"status": "success", "message": "Bots detenidos."}

@app.post("/api/start")
def start_bots_api(config: BotConfig):
    global_stop_event.clear()
    active_bots_list.clear()
    
    api_log(f"[*] Lanzando {config.bots} bot(s) hacia {config.host}:{config.port}")
    
    shared_deadline = time.monotonic() + config.hold + (config.bots * config.coldown) + 8.0
    
    def launch_sequence():
        for i in range(config.bots):
            if global_stop_event.is_set():
                break
            
            bot_name = (
                f"{config.name}{i + 1}_{random.randint(100, 999)}"
                if config.bots > 1 else
                f"{config.name}_{random.randint(100, 999)}"
            )

            bot = BackendBot(
                idx=i,
                host=config.host,
                port=config.port,
                username=bot_name,
                timeout=config.timeout,
                hold=config.hold,
                log_callback=api_log,
                stop_event=global_stop_event,
                hold_deadline=shared_deadline
            )
            
            active_bots_list.append(bot)
            threading.Thread(target=bot.run, daemon=True).start()

            if i < config.bots - 1:
                time.sleep(config.coldown)

    threading.Thread(target=launch_sequence, daemon=True).start()
    
    return {
        "status": "success",
        "message": f"Iniciando {config.bots} bot(s) correctamente hacia {config.host}:{config.port}"
    }

# ─────────────────────────────────────────────
# Protocolo RakNet y Constantes Originales
# ─────────────────────────────────────────────
RAKNET_MAGIC = bytes([
    0x00, 0xFF, 0xFF, 0x00, 0xFE, 0xFE, 0xFE, 0xFE,
    0xFD, 0xFD, 0xFD, 0xFD, 0x12, 0x34, 0x56, 0x78
])
ID_OPEN_CONNECTION_REQUEST_1 = 0x05
ID_OPEN_CONNECTION_REPLY_1 = 0x06
ID_OPEN_CONNECTION_REQUEST_2 = 0x07
ID_OPEN_CONNECTION_REPLY_2 = 0x08
ID_CONNECTION_REQUEST = 0x09
ID_CONNECTION_REQUEST_ACCEPTED = 0x10
ID_NEW_INCOMING_CONNECTION = 0x13
ID_DISCONNECTION_NOTIFICATION = 0x15
RAKNET_PROTOCOL_VERSION = 10

RELIABILITY_UNRELIABLE = 0
RELIABILITY_RELIABLE = 2
RELIABILITY_RELIABLE_ORDERED = 3
MTU = 1200

def enc_addr(ip: str, port: int) -> bytes:
    parts = [int(x) for x in ip.split('.')]
    packed = struct.pack('!B', 4)
    for p in parts:
        packed += struct.pack('!B', (~p) & 0xFF)
    packed += struct.pack('!H', port)
    return packed

def dummy_addr() -> bytes:
    return struct.pack('!B', 4) + bytes([0xFF, 0xFF, 0xFF, 0xFF]) + struct.pack('!H', 0)

def gen_login_payload(username: str, host: str, port: int, protocol: int = 84):
    priv = ec.generate_private_key(ec.SECP384R1())
    pub_der = priv.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    x5u = base64.b64encode(pub_der).decode().rstrip("=")

    identity_uuid = str(uuid.uuid4())
    now = int(time.time())

    chain_payload = {
        "certificateAuthority": True,
        "exp": now + 24 * 3600,
        "identityPublicKey": x5u,
        "nbf": now - 3600,
        "extraData": {
            "XUID": "",
            "identity": identity_uuid,
            "displayName": username,
            "titleId": "896928775",
        },
    }
    chain_jwt = pyjwt.encode(chain_payload, priv, algorithm="ES384", headers={"x5u": x5u})
    chain_json = json.dumps({"chain": [chain_jwt]}).encode("utf-8")

    w, h = 64, 64
    skin_bytes = bytearray(w * h * 4)
    for i in range(0, len(skin_bytes), 4):
        skin_bytes[i] = 0xC6
        skin_bytes[i + 1] = 0x8A
        skin_bytes[i + 2] = 0x5A
        skin_bytes[i + 3] = 0xFF
    skin_bytes = bytes(skin_bytes)
    skin_b64 = base64.b64encode(skin_bytes).decode()

    client_random_id = random.getrandbits(63) * random.choice([1, -1])
    client_data_payload = {
        "ClientRandomId": client_random_id,
        "CurrentInputMode": 1,
        "DefaultInputMode": 1,
        "DeviceModel": "",
        "DeviceOS": 7,
        "GameVersion": "0.15.10",
        "GuiScale": 0,
        "LanguageCode": "en_US",
        "ServerAddress": f"{host}:{port}",
        "SkinId": "Standard_Alex",
        "SkinData": skin_b64,
        "SkinGeometryName": "geometry.humanoid.customSlim",
        "ThirdPartyName": username,
        "UIProfile": 0,
    }
    client_jwt = pyjwt.encode(client_data_payload, priv, algorithm="ES384", headers={"x5u": x5u})
    if isinstance(client_jwt, bytes):
        client_jwt = client_jwt.decode()

    inner = struct.pack("<I", len(chain_json)) + chain_json
    inner += struct.pack("<I", len(client_jwt)) + client_jwt.encode("ascii")
    compressed = zlib.compress(inner, 6)

    body = struct.pack(">I", protocol) + struct.pack(">I", len(compressed)) + compressed
    return bytes([0x01]) + body

def build_batch(inner_packets: list) -> bytes:
    payload = b""
    for pkt in inner_packets:
        payload += struct.pack(">I", len(pkt)) + pkt
    compressed = zlib.compress(payload, 6)
    return bytes([0xFE, 0x06]) + struct.pack(">I", len(compressed)) + compressed

class BinaryStream:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def read_byte(self) -> int:
        if self.offset >= len(self.data):
            raise IndexError("Fin de stream")
        val = self.data[self.offset]
        self.offset += 1
        return val

# ─────────────────────────────────────────────
# Motor del Bot (Basado en la lógica original)
# ─────────────────────────────────────────────
class BackendBot:
    def __init__(self, idx, host, port, username, timeout, hold, log_callback, stop_event, hold_deadline=None):
        self.idx = idx
        self.host = host.strip()
        self.port = port
        self.username = username
        self.timeout = timeout
        self.hold = hold
        self.hold_deadline = hold_deadline
        self.log_callback = log_callback
        self.stop_event = stop_event

        self.sock = None
        self.target_address = (self.host, self.port)
        self.client_guid = random.getrandbits(64)
        self.send_seq = 0
        self.message_index = 0
        self.order_index = 0
        self.split_id_counter = 0
        self.initialized = False
        self.entity_id = 1
        self.pos_x = 0.0
        self.pos_y = 64.0
        self.pos_z = 0.0

    def log(self, msg):
        if self.log_callback:
            self.log_callback(f"[{self.username}] {msg}")

    def send_raw(self, data):
        if not self.sock:
            return
        try:
            self.sock.sendto(data, self.target_address)
        except Exception:
            pass

    def recv_raw(self):
        data, _ = self.sock.recvfrom(4096)
        return data

    def send_ack(self, seq):
        pkt = bytes([0xC0]) + struct.pack("<H", 1) + bytes([1]) + struct.pack("<I", seq)[:3]
        self.send_raw(pkt)

    def send_encapsulated(self, payload: bytes, reliability=RELIABILITY_RELIABLE_ORDERED):
        chunks = [payload[i:i + MTU] for i in range(0, len(payload), MTU)] or [b""]
        has_split = len(chunks) > 1
        split_id = self.split_id_counter
        self.split_id_counter = (self.split_id_counter + 1) & 0xFFFF

        for i, chunk in enumerate(chunks):
            if self.stop_event.is_set():
                break
            flags = (reliability << 5) | (0x10 if has_split else 0)
            body = bytes([flags]) + struct.pack(">H", len(chunk) * 8)
            if reliability in (2, 3):
                body += struct.pack("<I", self.message_index)[:3]
                self.message_index += 1
            if reliability == 3:
                body += struct.pack("<I", self.order_index)[:3] + bytes([0])
            if has_split:
                body += struct.pack(">I", len(chunks)) + struct.pack(">H", split_id) + struct.pack(">I", i)
            body += chunk

            datagram = bytes([0x84]) + struct.pack("<I", self.send_seq)[:3] + body
            try:
                self.send_raw(datagram)
            except Exception:
                pass
            self.send_seq += 1

        if reliability == 3:
            self.order_index += 1

    def send_move_player(self, mode=0):
        try:
            eye_y = self.pos_y + 1.62
            pkt = bytes([0x10])
            pkt += struct.pack(">Q", int(self.entity_id) & 0xFFFFFFFFFFFFFFFF)
            pkt += struct.pack(">f", self.pos_x) + struct.pack(">f", eye_y) + struct.pack(">f", self.pos_z)
            pkt += struct.pack(">f", 0.0) + struct.pack(">f", 0.0) + struct.pack(">f", 0.0)
            pkt += bytes([mode & 0xFF]) + bytes([1])
            batch = build_batch([pkt])
            self.send_encapsulated(batch, reliability=RELIABILITY_RELIABLE_ORDERED)
        except Exception:
            pass

    def send_initialization_packets(self):
        try:
            radius_pkt = bytes([0x3d]) + struct.pack(">i", 8)
            batch = build_batch([radius_pkt])
            self.send_encapsulated(batch, reliability=RELIABILITY_RELIABLE_ORDERED)
            self.initialized = True
            self.send_move_player(mode=1)
        except Exception as e:
            self.log(f"Error inicialización: {e}")

    def send_disconnect(self):
        try:
            self.send_encapsulated(bytes([ID_DISCONNECTION_NOTIFICATION]), reliability=RELIABILITY_RELIABLE)
        except Exception:
            pass

    def run(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.settimeout(self.timeout)
            self.sock.bind(("0.0.0.0", 0))

            self._handshake_offline()
            if self.stop_event.is_set():
                return
            self._handshake_connected()
            if self.stop_event.is_set():
                return
            self._do_login()
            self._keep_alive()
        except Exception as e:
            self.log(f"Desconectado / Error: {e}")
        finally:
            try:
                self.send_disconnect()
                if self.sock:
                    self.sock.close()
            except Exception:
                pass

    def _handshake_offline(self):
        payload = struct.pack("B", ID_OPEN_CONNECTION_REQUEST_1) + RAKNET_MAGIC
        payload += struct.pack("B", RAKNET_PROTOCOL_VERSION) + b"\x00" * (MTU - len(payload) - 28)
        self.send_raw(payload)
        data = self.recv_raw()
        if data[0] != ID_OPEN_CONNECTION_REPLY_1:
            raise RuntimeError("REPLY_1 inválido")
        server_mtu = struct.unpack("!H", data[26:28])[0]

        payload2 = struct.pack("B", ID_OPEN_CONNECTION_REQUEST_2) + RAKNET_MAGIC
        payload2 += enc_addr(self.host, self.port) + struct.pack("!H", server_mtu) + struct.pack("!Q", self.client_guid)
        self.send_raw(payload2)
        data2 = self.recv_raw()
        if data2[0] != ID_OPEN_CONNECTION_REPLY_2:
            raise RuntimeError("REPLY_2 inválido")

    def _handshake_connected(self):
        req = bytes([ID_CONNECTION_REQUEST]) + struct.pack("!Q", self.client_guid) + struct.pack("!Q", int(time.time() * 1000)) + bytes([0])
        self.send_encapsulated(req, reliability=RELIABILITY_RELIABLE)

        deadline = time.time() + self.timeout
        got_accept = False
        while time.time() < deadline and not got_accept and not self.stop_event.is_set():
            try:
                data = self.recv_raw()
                for payload in self._extract_payloads(data):
                    if payload and payload[0] == ID_CONNECTION_REQUEST_ACCEPTED:
                        got_accept = True
                        self._send_new_incoming(payload)
            except socket.timeout:
                continue
        if not got_accept:
            raise RuntimeError("No ConnectionRequestAccepted")

    def _send_new_incoming(self, accepted_payload):
        server_time = struct.unpack("!Q", accepted_payload[-8:])[0]
        nic = enc_addr(self.host, self.port)
        for _ in range(10):
            nic += dummy_addr()
        nic += struct.pack("!Q", server_time) + struct.pack("!Q", int(time.time() * 1000))
        pkt = bytes([ID_NEW_INCOMING_CONNECTION]) + nic
        self.send_encapsulated(pkt, reliability=RELIABILITY_RELIABLE)

    def _do_login(self):
        login_pkt = gen_login_payload(self.username, self.host, self.port)
        batch = build_batch([login_pkt])
        self.send_encapsulated(batch, reliability=RELIABILITY_RELIABLE_ORDERED)

    def _extract_payloads(self, data):
        if not data or data[0] & 0xC0 != 0x80:
            return []
        seq = data[1] | (data[2] << 8) | (data[3] << 16)
        self.send_ack(seq)
        off = 4
        out = []
        while off < len(data):
            try:
                flags = data[off]
                off += 1
                reliability = (flags & 0b11100000) >> 5
                has_split = (flags & 0b00010000) != 0
                length_bits = struct.unpack(">H", data[off:off + 2])[0]
                off += 2
                length_bytes = (length_bits + 7) // 8
                if reliability in (2, 3, 4, 6, 7):
                    off += 3
                if reliability in (1, 3, 4, 7):
                    off += 4
                if has_split:
                    off += 10

                payload_chunk = data[off:off + length_bytes]
                out.append(payload_chunk)
                if len(payload_chunk) > 0 and payload_chunk[0] == 0x09 and not self.initialized:
                    self.send_initialization_packets()
                off += length_bytes
            except Exception:
                break
        return out

    def _keep_alive(self):
        end = float(self.hold_deadline) if self.hold_deadline else (time.monotonic() + float(self.hold))
        while not self.stop_event.is_set():
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            try:
                self.sock.settimeout(min(0.2, max(0.05, remaining)))
                data = self.recv_raw()
                self._extract_payloads(data)
            except socket.timeout:
                continue
            except Exception:
                break
        self.send_disconnect()
        self.log("Sesión finalizada.")