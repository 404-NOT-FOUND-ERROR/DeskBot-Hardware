"""Create a local HTTPS trial profile. Prints paths and public facts only."""
import argparse
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import secrets
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]
SERVER_NAME = "deskbot-touch.local"
DEVICE = "device-9046b3127b3c912b392774b8ffadd819"


def prepare(folder, host, port, *, lab=False):
    address = ipaddress.IPv4Address(host)
    octets = address.packed
    private = octets[0] == 10 or (octets[0] == 172 and 16 <= octets[1] <= 31) or octets[:2] == bytes((192, 168))
    if not (private or lab and address == ipaddress.IPv4Address("127.0.0.1")):
        raise ValueError("choose_private_wifi_ipv4")
    if not 1 <= port <= 65535:
        raise ValueError("invalid_port")
    folder.mkdir(parents=True, exist_ok=False)
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, SERVER_NAME)])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=90))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(SERVER_NAME), x509.IPAddress(address),
                                                   x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]), False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
        .sign(key, hashes.SHA256()))
    cert = certificate.public_bytes(serialization.Encoding.PEM).decode()
    (folder / "server-cert.pem").write_text(cert, encoding="ascii")
    (folder / "server-key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    token = secrets.token_hex(32)
    (folder / "device-token.txt").write_text(token, encoding="ascii")
    config = dict(schema="deskbot.head-touch-wifi-config.v1", host=host, port=port, device_id=DEVICE,
                  firmware_sha256=None, token_file="device-token.txt", cert_file="server-cert.pem",
                  key_file="server-key.pem", lab=lab)
    (folder / "receiver.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    header = (f'#pragma once\ninline constexpr char kTouchUrl[] = "https://{host}:{port}/api/hardware/head-touch";\n'
              f'inline constexpr char kTouchServerName[] = "{SERVER_NAME}";\n'
              f'inline constexpr char kTouchAuthorization[] = "Bearer {token}";\n'
              f'inline constexpr char kTouchCa[] = R"CERT({cert})CERT";\n')
    (folder / "head_touch_wifi.local.h").write_text(header, encoding="utf-8")
    return {"profile": str(folder), "host": host, "port": port, "lab": lab,
            "ready_for": "offline_build" if lab else "firmware_build"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=4318)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--lab", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prepare(args.profile.resolve(), args.host, args.port, lab=args.lab)))
