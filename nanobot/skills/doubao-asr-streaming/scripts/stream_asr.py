#!/usr/bin/env python3
"""Stream local audio file to Doubao ASR Realtime API and return transcript.

This script intentionally keeps credentials in env vars so users can configure
per their own Volcengine account.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import gzip
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import websockets

DEFAULT_URL_BEARER = "wss://ark.cn-beijing.volces.com/api/v3/realtime"
DEFAULT_URL_APP = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
APP_FALLBACK_URLS = [
    "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
    "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel",
    "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream",
]
DEFAULT_MODEL = "volc.seedasr.sauc.duration"


class ConfigError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert an audio file to text through Doubao streaming ASR API."
    )
    parser.add_argument("input", type=Path, help="Path to input audio file")
    parser.add_argument("-o", "--output", type=Path, help="Write transcript to this file")
    parser.add_argument(
        "--url",
        default=os.getenv("DOUBAO_REALTIME_URL", ""),
        help="Realtime websocket URL (optional, auto-picked from auth mode when omitted)",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("DOUBAO_ASR_MODEL", DEFAULT_MODEL),
        help="ASR model resource id (default volc.seedasr.sauc.duration)",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("DOUBAO_API_KEY", ""),
        help="Bearer API key for Ark Realtime gateway",
    )
    parser.add_argument(
        "--app-key",
        default=os.getenv("DOUBAO_APP_KEY", os.getenv("DOUBAO_APP_ID", "")),
        help="App key/app id header (maps to console APP ID)",
    )
    parser.add_argument(
        "--access-key",
        default=os.getenv("DOUBAO_ACCESS_KEY", ""),
        help="Access key header (maps to console Access Token)",
    )
    parser.add_argument(
        "--secret-key",
        default=os.getenv("DOUBAO_SECRET_KEY", ""),
        help="Optional secret key placeholder (some gateways require signing)",
    )
    parser.add_argument(
        "--resource-id",
        default=os.getenv("DOUBAO_RESOURCE_ID", DEFAULT_MODEL),
        help="X-Api-Resource-Id for app/access mode (default volc.seedasr.sauc.duration)",
    )
    parser.add_argument(
        "--language",
        default=os.getenv("DOUBAO_ASR_LANGUAGE", "zh"),
        help="Language hint (zh/en/...)",
    )
    parser.add_argument(
        "--chunk-ms",
        type=int,
        default=100,
        help="Chunk size in milliseconds for each append event",
    )
    parser.add_argument(
        "--realtime",
        action="store_true",
        help="Sleep between chunks to mimic realtime sending",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed websocket events",
    )
    return parser.parse_args()


def resolve_url(args: argparse.Namespace) -> str:
    if args.url:
        return args.url
    if args.api_key:
        return DEFAULT_URL_BEARER
    return DEFAULT_URL_APP


def build_candidate_urls(args: argparse.Namespace, headers: dict[str, str]) -> list[str]:
    if args.url:
        return [args.url]
    if "Authorization" in headers:
        return [DEFAULT_URL_BEARER]
    return APP_FALLBACK_URLS


def build_headers(args: argparse.Namespace) -> dict[str, str]:
    headers: dict[str, str] = {}
    if args.api_key and not (args.app_key and args.access_key):
        headers["Authorization"] = f"Bearer {args.api_key}"

    if args.app_key and args.access_key:
        headers["X-Api-App-Key"] = args.app_key
        headers["X-Api-Access-Key"] = args.access_key
        headers["X-Api-Resource-Id"] = args.resource_id
        headers["X-Api-Connect-Id"] = str(uuid.uuid4())
        headers["X-Api-Request-Id"] = str(uuid.uuid4())

    # Some enterprise gateways use custom signed headers. Keep this explicit so
    # users can see where to inject signing logic if needed.
    if args.secret_key:
        headers["X-Api-Secret-Key"] = args.secret_key

    if not headers:
        raise ConfigError(
            "No credential found. Set DOUBAO_APP_KEY + DOUBAO_ACCESS_KEY (recommended), "
            "or DOUBAO_API_KEY for Ark bearer mode."
        )
    return headers


def ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise ConfigError(
            "ffmpeg not found. Install ffmpeg to transcode input audio to pcm_s16le/16k/mono."
        )


def transcode_to_pcm16(input_path: Path) -> bytes:
    if not input_path.exists():
        raise ConfigError(f"Input file does not exist: {input_path}")

    ensure_ffmpeg()
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"ffmpeg failed: {stderr or 'unknown error'}")
    return proc.stdout


def build_session_update(model: str, language: str) -> dict:
    return {
        "type": "transcription_session.update",
        "session": {
            "input_audio_format": "pcm16",
            "input_audio_transcription": {
                "model": model,
                "language": language,
            },
        },
    }


def _pack_full_client_request(payload_obj: dict) -> bytes:
    payload = json.dumps(payload_obj, ensure_ascii=False).encode("utf-8")
    # version=1, header_size=1(4B), type=1(full request), flags=0, serialization=json, compression=none
    header = bytes([0x11, 0x10, 0x10, 0x00])
    return header + len(payload).to_bytes(4, "big", signed=False) + payload


def _pack_audio_only_request(audio: bytes, sequence: int, final: bool) -> bytes:
    # type=2(audio only), flags=1(seq>0) or 3(seq<0 final)
    flag = 0x03 if final else 0x01
    header = bytes([0x11, (0x20 | flag), 0x00, 0x00])
    seq = -abs(sequence) if final else abs(sequence)
    return (
        header
        + seq.to_bytes(4, "big", signed=True)
        + len(audio).to_bytes(4, "big", signed=False)
        + audio
    )


def _build_app_mode_submit_payload(args: argparse.Namespace) -> dict:
    reqid = str(uuid.uuid4())
    return {
        "app": {
            "appid": args.app_key,
            "token": args.access_key,
            "cluster": args.resource_id,
        },
        "user": {"uid": "nanobot"},
        "audio": {
            "format": "pcm",
            "rate": 16000,
            "bits": 16,
            "channel": 1,
            "codec": "raw",
        },
        "request": {
            "reqid": reqid,
            "sequence": 1,
            "operation": "submit",
            "model_name": args.model,
            "language": args.language,
            "enable_itn": True,
            "enable_punc": True,
        },
    }


def _decode_binary_event(raw: bytes) -> dict:
    """Best-effort decoder for Volcengine speech binary websocket frames."""
    if len(raw) < 4:
        return {"type": "_binary", "len": len(raw), "hex": raw.hex()}

    protocol_version = raw[0] >> 4
    header_words = raw[0] & 0x0F
    message_type = raw[1] >> 4
    message_flags = raw[1] & 0x0F
    serialization = raw[2] >> 4
    compression = raw[2] & 0x0F
    header_len = header_words * 4
    payload = raw[header_len:] if header_len <= len(raw) else b""

    # Try common payload layouts:
    # 1) payload is direct body
    # 2) payload[0:4] is body length
    # 3) payload[4:8] is body length (common in speech protocol error frames)
    candidates = [payload]
    if len(payload) >= 4:
        size0 = int.from_bytes(payload[:4], "big", signed=False)
        if 0 < size0 <= len(payload) - 4:
            candidates.insert(0, payload[4 : 4 + size0])
    if len(payload) >= 8:
        size4 = int.from_bytes(payload[4:8], "big", signed=False)
        if 0 < size4 <= len(payload) - 8:
            candidates.insert(0, payload[8 : 8 + size4])

    for candidate in candidates:
        body = candidate
        if compression in {1, 2}:
            try:
                body = gzip.decompress(body)
            except Exception:  # noqa: BLE001
                pass

        try:
            text = body.decode("utf-8")
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    parsed.setdefault("type", "error" if message_type == 15 else "_binary_json")
                    parsed["_meta"] = {
                        "protocol_version": protocol_version,
                        "message_type": message_type,
                        "message_flags": message_flags,
                        "serialization": serialization,
                        "compression": compression,
                    }
                    return parsed
            except Exception:  # noqa: BLE001
                return {
                    "type": "_binary_text",
                    "text": text,
                    "_meta": {
                        "protocol_version": protocol_version,
                        "message_type": message_type,
                        "message_flags": message_flags,
                        "serialization": serialization,
                        "compression": compression,
                    },
                }
        except Exception:  # noqa: BLE001
            continue

    return {
        "type": "_binary",
        "len": len(raw),
        "head_hex": raw[:24].hex(),
        "_meta": {
            "protocol_version": protocol_version,
            "message_type": message_type,
            "message_flags": message_flags,
            "serialization": serialization,
            "compression": compression,
        },
    }


def _extract_text_from_event(event: dict) -> list[str]:
    out: list[str] = []
    if "transcript" in event and isinstance(event.get("transcript"), str):
        t = event["transcript"].strip()
        if t:
            out.append(t)
    result = event.get("result")
    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict):
                text = item.get("text") or item.get("utterance")
                if isinstance(text, str) and text.strip():
                    out.append(text.strip())
    if isinstance(result, dict):
        text = result.get("text") or result.get("utterance")
        if isinstance(text, str) and text.strip():
            out.append(text.strip())
    return out


async def run_stream(args: argparse.Namespace, pcm_bytes: bytes) -> str:
    headers = build_headers(args)
    candidate_urls = build_candidate_urls(args, headers)

    chunk_bytes = max(1, int((16000 * 2) * (args.chunk_ms / 1000.0)))
    mode = "app/access mode" if "X-Api-App-Key" in headers else "bearer mode"
    safe_headers = {k: ("***" if "Key" in k or "Authorization" in k else v) for k, v in headers.items()}
    if args.verbose:
        print(f"[debug] mode={mode}", file=sys.stderr)
        print(f"[debug] headers={json.dumps(safe_headers, ensure_ascii=False)}", file=sys.stderr)

    errors: list[str] = []
    saw_non_error_response = False
    for base_url in candidate_urls:
        ws_url = f"{base_url}?model={args.model}" if "ark.cn-beijing.volces.com" in base_url else base_url
        if args.verbose:
            print(f"[debug] ws_url={ws_url}", file=sys.stderr)

        transcripts: list[str] = []
        try:
            async with websockets.connect(
                ws_url, additional_headers=headers, max_size=10 * 1024 * 1024
            ) as ws:
                is_bearer = "Authorization" in headers
                if is_bearer:
                    await ws.send(json.dumps(build_session_update(args.model, args.language)))

                    for i in range(0, len(pcm_bytes), chunk_bytes):
                        chunk = pcm_bytes[i : i + chunk_bytes]
                        audio_b64 = base64.b64encode(chunk).decode("utf-8")
                        await ws.send(
                            json.dumps({"type": "input_audio_buffer.append", "audio": audio_b64})
                        )
                        if args.realtime:
                            await asyncio.sleep(args.chunk_ms / 1000)

                    await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                    await ws.send(json.dumps({"type": "response.create"}))
                else:
                    # App/access mode uses binary websocket protocol.
                    await ws.send(_pack_full_client_request(_build_app_mode_submit_payload(args)))
                    seq = 2
                    for i in range(0, len(pcm_bytes), chunk_bytes):
                        chunk = pcm_bytes[i : i + chunk_bytes]
                        await ws.send(_pack_audio_only_request(chunk, seq, final=False))
                        seq += 1
                        if args.realtime:
                            await asyncio.sleep(args.chunk_ms / 1000)
                    await ws.send(_pack_audio_only_request(b"", seq, final=True))

                while True:
                    try:
                        raw = await ws.recv()
                    except websockets.exceptions.ConnectionClosedOK:
                        # Normal server-side close after final result.
                        break
                    if isinstance(raw, bytes):
                        event = _decode_binary_event(raw)
                    else:
                        event = json.loads(raw)
                    event_type = event.get("type", "")
                    saw_non_error_response = True

                    if args.verbose:
                        print(json.dumps(event, ensure_ascii=False), file=sys.stderr)

                    if event_type == "conversation.item.input_audio_transcription.failed":
                        raise RuntimeError(f"ASR failed: {json.dumps(event, ensure_ascii=False)}")
                    elif event_type in {"error", "server.error", "_binary_json"}:
                        # Surface detailed server-side diagnostics early.
                        if any(k in event for k in ("code", "message", "error", "status_code")):
                            raise RuntimeError(
                                f"ASR server error: {json.dumps(event, ensure_ascii=False)}"
                            )
                    elif event_type in {"response.completed", "response.done"}:
                        break
                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        transcript = event.get("transcript", "").strip()
                        if transcript:
                            transcripts.append(transcript)
                    else:
                        transcripts.extend(_extract_text_from_event(event))
            if transcripts:
                return "\n".join(transcripts).strip()
            # This endpoint completed but returned no transcript text.
            # Try next candidate endpoint if available.
            continue
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{ws_url}: {exc}")
            continue

    if saw_non_error_response:
        return ""
    raise RuntimeError("All websocket endpoints failed: " + " | ".join(errors))


def main() -> int:
    args = parse_args()

    try:
        pcm = transcode_to_pcm16(args.input)
        duration_sec = len(pcm) / (16000 * 2)
        if duration_sec < 0.2:
            raise ConfigError(
                f"Input audio is too short ({duration_sec:.3f}s). Provide a longer sample (>= 1s)."
            )
        text = asyncio.run(run_stream(args, pcm))
    except ConfigError as exc:
        print(f"[config-error] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"[asr-error] {exc}", file=sys.stderr)
        return 1

    if args.output:
        args.output.write_text(text + ("\n" if text else ""), encoding="utf-8")

    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
