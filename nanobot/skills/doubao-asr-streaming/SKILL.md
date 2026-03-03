---
name: doubao-asr-streaming
description: Convert local audio files to text with Doubao streaming ASR API. Use when users ask for real-time/streaming transcription from audio/video files and need websocket chunk upload, transcript aggregation, and credential configuration guidance.
---

# Doubao Streaming ASR

Use this skill to transcribe a local file through Doubao streaming ASR and return plain text.

## Install Dependencies First

Always install dependencies before first run:

```bash
bash nanobot/skills/doubao-asr-streaming/scripts/install_deps.sh
```

If install fails due to permissions/network, report exact failing command and ask user to run it manually.

## Ask For User Configuration

Always ask the user to configure credentials themselves before execution.

Required env vars (recommended mode):

- `DOUBAO_APP_KEY`: Map to console `APP ID`, sent as `X-Api-App-Key`.
- `DOUBAO_ACCESS_KEY`: Map to console `Access Token`, sent as `X-Api-Access-Key`.

Optional env vars:
- `DOUBAO_RESOURCE_ID`: Sent as `X-Api-Resource-Id`, default `volc.seedasr.sauc.duration`.
- `DOUBAO_SECRET_KEY`: Secret key placeholder for custom signing flow.
- `DOUBAO_REALTIME_URL`: WebSocket URL override. When omitted:
  - app/access mode -> auto try `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async`, `.../bigmodel`, `.../bigmodel_nostream`
  - bearer mode -> `wss://ark.cn-beijing.volces.com/api/v3/realtime`
- `DOUBAO_ASR_MODEL`: Override model when needed. Default is `volc.seedasr.sauc.duration` (Doubao streaming ASR 2.0 hour model), so users usually do not need to set this.
- `DOUBAO_ASR_LANGUAGE`: Language hint, default `zh`.
- `DOUBAO_API_KEY`: Ark bearer token (only for bearer mode, not required in app/access mode).

If credentials are missing, stop and tell the user exactly which variable is missing.

Recommended prompt text for users:

```bash
export DOUBAO_APP_KEY='YOUR_APP_ID'
export DOUBAO_ACCESS_KEY='YOUR_ACCESS_TOKEN'
# Optional:
# export DOUBAO_RESOURCE_ID='volc.seedasr.sauc.duration'
```

## Execute Transcription

Run:

```bash
python3 nanobot/skills/doubao-asr-streaming/scripts/stream_asr.py \
  /path/to/input.wav \
  --output /path/to/transcript.txt
```

Useful flags:

- `--model volc.seedasr.sauc.duration`
- `--chunk-ms 100`
- `--realtime` to mimic live upload timing
- `--verbose` to inspect websocket events

## Behavior

- Transcode input audio with `ffmpeg` to `pcm_s16le`, 16kHz, mono.
- Connect to realtime websocket and send `transcription_session.update`.
- Stream audio via `input_audio_buffer.append` chunks.
- Finalize with `input_audio_buffer.commit` and `response.create`.
- Aggregate `conversation.item.input_audio_transcription.completed` events to final text.

## Troubleshooting

- If `ffmpeg` is missing: install it first.
- If auth fails: verify URL/headers and rotate keys.
- If auth fails with `401`: verify `DOUBAO_APP_KEY/DOUBAO_ACCESS_KEY` and ensure `X-Api-Resource-Id` is `volc.seedasr.sauc.duration`.
- If model is rejected: confirm account access to `volc.seedasr.sauc.duration`; if your account only has another resource id, override `--model`.
- If result is empty: check source audio duration first (`ffprobe`). Samples under 1 second usually produce empty output.
- If gateway requires signed headers, keep this skill's script as base and add signing logic where headers are built.

## Doc

Primary doc URL (user provided):

- `https://www.volcengine.com/docs/6561/1354869?lang=zh`
