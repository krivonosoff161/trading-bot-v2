# Snapshot Coverage Audit - WS Main

Source files:

- labels: `logs\signals\main_signals_labels.jsonl`
- signals: `logs\signals\main_signals.jsonl`
- snapshots: `logs\signals\signal_snapshot.jsonl`

## Summary

| Metric | Value |
| --- | ---: |
| labeled WS signals | 85 |
| raw snapshots, all sources | 61 |
| excluded non-ws_main snapshots | 2 |
| matching snapshots | 59 |
| missing context | 26 |
| snapshot coverage | 69.4% |

Context-based analysis below is partial by construction. Missing snapshots must not be assumed random.
The two excluded snapshots are `source=ws_scanner` and do not join to `main_signals_labels.jsonl`; they are not WS-main truth.

## Missing By Regime

| Key | Missing context |
| --- | ---: |
| DRIFT | 17 |
| TRENDING | 8 |
| RANGING | 1 |

## Missing By Style

| Key | Missing context |
| --- | ---: |
| FAST | 18 |
| SWING | 8 |

## Missing By Pair

| Key | Missing context |
| --- | ---: |
| MEW-USDT-SWAP | 3 |
| BTC-USDT-SWAP | 2 |
| PENGU-USDT-SWAP | 2 |
| LAYER-USDT-SWAP | 2 |
| ADA-USDT-SWAP | 2 |
| ETH-USDT-SWAP | 2 |
| NEIRO-USDT-SWAP | 2 |
| BOME-USDT-SWAP | 2 |
| PUMP-USDT-SWAP | 2 |
| PLUME-USDT-SWAP | 1 |
| CHIP-USDT-SWAP | 1 |
| TRUTH-USDT-SWAP | 1 |
| XRP-USDT-SWAP | 1 |
| LINEA-USDT-SWAP | 1 |
| TURBO-USDT-SWAP | 1 |
| MEME-USDT-SWAP | 1 |

## Missing Rows

| signal_id | ts | symbol | regime | style | outcome |
| --- | --- | --- | --- | --- | --- |
| b28e578c-3fe6-4ef4-8461-9d96bc6b2142 | 2026-05-09T15:00:01Z | PLUME-USDT-SWAP | TRENDING | SWING | TP1 |
| 23d13392-4ab0-416b-b266-4de3036a75bc | 2026-05-09T15:45:00Z | BTC-USDT-SWAP | DRIFT | FAST | TP1 |
| 2b202e66-0b24-4d57-8773-fb6b7d68ccd0 | 2026-05-09T17:30:00Z | BTC-USDT-SWAP | DRIFT | FAST | TP1 |
| 82c631c7-8483-4687-baee-8cb4741d188f | 2026-05-09T18:45:01Z | CHIP-USDT-SWAP | RANGING | FAST | TIME |
| 7aadcf43-14f4-4bbf-9636-07758c786335 | 2026-05-09T22:00:01Z | PENGU-USDT-SWAP | DRIFT | FAST | TIME |
| 79bf5a2f-565e-4447-89b3-5af9cd72e5c9 | 2026-05-10T03:00:01Z | MEW-USDT-SWAP | TRENDING | SWING | SL |
| 0b4103d0-e5fa-42f3-80ad-381d55f77515 | 2026-05-10T08:15:00Z | LAYER-USDT-SWAP | TRENDING | SWING | TIME |
| 0c8502d1-e47c-4020-9afb-f9a6778bb0df | 2026-05-10T11:00:00Z | ADA-USDT-SWAP | DRIFT | FAST | TIME |
| 31823e51-db50-4f67-8ccf-61d29229acda | 2026-05-10T11:00:00Z | MEW-USDT-SWAP | TRENDING | SWING | SL |
| 88b2bed3-938d-429d-a2b7-bc25e8608b15 | 2026-05-10T13:00:00Z | TRUTH-USDT-SWAP | TRENDING | SWING | TP1 |
| 236f2b7a-8968-48f9-b76d-851a1ee506aa | 2026-05-10T15:30:00Z | XRP-USDT-SWAP | DRIFT | FAST | TP2 |
| 715547fc-c40a-4ae7-97f8-a251636033af | 2026-05-10T15:30:00Z | ETH-USDT-SWAP | DRIFT | FAST | TP2 |
| 9f24c908-ec88-4fe1-ac03-7e25a56295ee | 2026-05-10T15:30:01Z | ADA-USDT-SWAP | DRIFT | FAST | TP2 |
| 4286bbfc-bb99-49e3-b897-be80a7d12388 | 2026-05-10T15:45:00Z | NEIRO-USDT-SWAP | DRIFT | FAST | TP2 |
| a06562bb-9724-440d-b440-37b305b9060c | 2026-05-10T16:15:00Z | MEW-USDT-SWAP | TRENDING | SWING | TP2 |
| ffc34c70-5544-4c27-99cd-885dcdefc5f7 | 2026-05-10T17:00:00Z | BOME-USDT-SWAP | DRIFT | FAST | TP2 |
| 07d08cf8-b241-4dff-939d-5a5a70fc7b72 | 2026-05-10T17:45:00Z | ETH-USDT-SWAP | DRIFT | FAST | TIME |
| fa2d8c2b-7d43-45bf-84bd-98f1158f2de6 | 2026-05-10T17:45:01Z | BOME-USDT-SWAP | DRIFT | FAST | TP1 |
| 0534a9b5-f399-47c9-8027-e10fdbab457a | 2026-05-10T18:00:00Z | PENGU-USDT-SWAP | DRIFT | FAST | TIME |
| dcbe3445-b6db-42a1-a46f-60317ca2de49 | 2026-05-10T18:00:00Z | LINEA-USDT-SWAP | DRIFT | FAST | TIME |
| 7202010e-9c48-454c-8644-460432bd503d | 2026-05-10T18:00:01Z | TURBO-USDT-SWAP | DRIFT | FAST | TP1 |
| e30d7d78-281f-4b33-b597-1b0232b5e911 | 2026-05-10T18:30:00Z | MEME-USDT-SWAP | DRIFT | FAST | TIME |
| 2b3a69ad-b5d9-4183-bc7b-c3c7090b761c | 2026-05-10T18:45:01Z | NEIRO-USDT-SWAP | DRIFT | FAST | TP2 |
| 2fb8ab8b-3fd1-4b4f-a02f-7aec4c8b76fc | 2026-05-10T19:45:01Z | PUMP-USDT-SWAP | TRENDING | SWING | SL |
| adff792c-298f-415f-8ed3-57b988a39e1f | 2026-05-10T20:00:00Z | PUMP-USDT-SWAP | TRENDING | SWING | SL |
| 60421fe4-f842-47fa-b6e7-22d65b79a577 | 2026-05-11T04:15:00Z | LAYER-USDT-SWAP | DRIFT | FAST | TIME |
