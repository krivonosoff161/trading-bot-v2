# Vendored: honest-backtest (`backtest_sanity`)

This is a **vendored, version-pinned copy** of the `backtest_sanity` package from the
`honest-backtest` project. It is vendored so that the calculation farm's hard-validation
bridge (`src/research_lab/honest_backtest_bridge.py`) is **reproducible on a clean
checkout / CI** instead of silently degrading to `NEEDS_MORE_DATA` when a developer-only
editable install is absent.

## Source

- Upstream repo: `honest-backtest` (github.com/krivonosoff161/honest-backtest)
- Package vendored: `backtest_sanity` only (the statistical core). The upstream
  `strategy_lab` CLI package is intentionally **not** vendored — the bridge does not need it.
- Pinned commit: `aaed510dfad7bd6157f0d8ed386ca5e1970cc891` (2026-06-14)
- Upstream version: `0.1.0`
- License: MIT (see `LICENSE` in this directory) — own code, vendoring is unrestricted.

## Dependencies

`backtest_sanity` is self-contained: standard library + `numpy` only (no third-party,
no `strategy_lab` import). `numpy==1.26.4` is already pinned in `requirements.txt`.

## How the bridge finds it

`honest_backtest_bridge.py` adds `vendor/honest-backtest/src` to `sys.path` (after an
optional `STRATEGY_LAB_HONEST_BACKTEST_SRC` override), so `import backtest_sanity` resolves
to this vendored copy first. If neither numpy nor this vendored package is importable, the
bridge **fails loud** (raises `BridgeUnavailableError`) instead of pretending the candidate
just needs more data — unless `STRATEGY_LAB_ALLOW_DEGRADED_VALIDATION=1` is set.

## Updating

To refresh against a newer upstream commit:

```bash
cp -r <honest-backtest>/src/backtest_sanity vendor/honest-backtest/src/backtest_sanity
cp <honest-backtest>/LICENSE vendor/honest-backtest/LICENSE
# update the pinned commit / version above
# remove any __pycache__ before committing
```

Do not edit the vendored sources in place; change upstream and re-vendor.
