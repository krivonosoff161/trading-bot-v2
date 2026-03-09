"""
OKX REST Client — minimal, clean, no strategy dependencies.
Handles: auth, market data, account info, order placement.
"""

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp
from loguru import logger


class OKXClient:
    BASE_URL_REAL = "https://www.okx.com"
    BASE_URL_DEMO = "https://www.okx.com"  # same URL, demo flag in header

    def __init__(self, api_key: str, secret_key: str, passphrase: str, is_demo: bool = True):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.is_demo = is_demo
        self.base_url = self.BASE_URL_DEMO if is_demo else self.BASE_URL_REAL
        self._session: Optional[aiohttp.ClientSession] = None
        logger.info("OKXClient initialized | demo={}", is_demo)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("OKXClient session closed")

    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        message = f"{timestamp}{method}{path}{body}"
        mac = hmac.new(
            self.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        )
        return base64.b64encode(mac.digest()).decode("utf-8")

    def _headers(self, method: str, path: str, body: str = "") -> dict:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        sign = self._sign(timestamp, method, path, body)
        headers = {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }
        if self.is_demo:
            headers["x-simulated-trading"] = "1"
        return headers

    async def _get(self, path: str, params: dict = None) -> dict:
        session = await self._get_session()
        query = ""
        if params:
            query = "?" + "&".join(f"{k}={v}" for k, v in params.items())
        full_path = path + query
        headers = self._headers("GET", full_path)
        url = self.base_url + full_path
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                if data.get("code") != "0":
                    logger.warning("OKX GET error | path={} code={} msg={}", path, data.get("code"), data.get("msg"))
                return data
        except Exception as e:
            logger.error("OKX GET failed | path={} error={}", path, e)
            return {}

    async def _post(self, path: str, body: dict) -> dict:
        session = await self._get_session()
        body_str = json.dumps(body)
        headers = self._headers("POST", path, body_str)
        url = self.base_url + path
        try:
            async with session.post(url, headers=headers, data=body_str, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                if data.get("code") != "0":
                    logger.warning("OKX POST error | path={} code={} msg={}", path, data.get("code"), data.get("msg"))
                return data
        except Exception as e:
            logger.error("OKX POST failed | path={} error={}", path, e)
            return {}

    # --- Instrument Info ---

    async def get_instrument_info(self, symbol: str) -> dict:
        """Return instrument spec (ctVal, lotSz, minSz) for a SWAP symbol."""
        inst_id = f"{symbol}-SWAP"
        data = await self._get("/api/v5/public/instruments", {"instType": "SWAP", "instId": inst_id})
        if data.get("code") == "0" and data.get("data"):
            return data["data"][0]
        logger.error("get_instrument_info failed | symbol={}", symbol)
        return {}

    # --- Market Data ---

    async def get_candles(self, symbol: str, bar: str = "5m", limit: int = 100) -> list:
        """Get OHLCV candles. Returns list of [ts, open, high, low, close, vol, ...]"""
        inst_id = f"{symbol}-SWAP"
        data = await self._get("/api/v5/market/candles", {"instId": inst_id, "bar": bar, "limit": str(limit)})
        if data.get("code") == "0" and data.get("data"):
            candles = data["data"]
            logger.debug("Candles | symbol={} bar={} count={}", symbol, bar, len(candles))
            return candles
        return []

    async def get_ticker(self, symbol: str) -> Optional[dict]:
        """Get current price for symbol."""
        inst_id = f"{symbol}-SWAP"
        data = await self._get("/api/v5/market/ticker", {"instId": inst_id})
        if data.get("code") == "0" and data.get("data"):
            ticker = data["data"][0]
            price = float(ticker.get("last", 0))
            logger.debug("Ticker | symbol={} price={}", symbol, price)
            return ticker
        return None

    async def get_price(self, symbol: str) -> Optional[float]:
        """Get last price as float."""
        ticker = await self.get_ticker(symbol)
        if ticker:
            return float(ticker.get("last", 0))
        return None

    # --- Account ---

    async def get_balance(self) -> Optional[float]:
        """Get USDT balance."""
        data = await self._get("/api/v5/account/balance", {"ccy": "USDT"})
        if data.get("code") == "0" and data.get("data"):
            details = data["data"][0].get("details", [])
            for d in details:
                if d.get("ccy") == "USDT":
                    balance = float(d.get("availBal", 0))
                    logger.debug("Balance | usdt={}", balance)
                    return balance
        return None

    async def get_positions(self, symbol: str) -> list:
        """Get open positions for symbol."""
        inst_id = f"{symbol}-SWAP"
        data = await self._get("/api/v5/account/positions", {"instId": inst_id})
        if data.get("code") == "0":
            return data.get("data", [])
        return []

    async def get_history_candles(
        self, symbol: str, bar: str = "5m",
        after: int = None, before: int = None, limit: int = 100,
    ) -> list:
        """Get historical OHLCV candles (public endpoint, supports time range).
        after  = return candles OLDER than this timestamp ms (exclusive)
        before = return candles NEWER than this timestamp ms (exclusive)
        To get 100 candles ending at ts: after=ts+1
        Returns list newest-first; each candle includes confirm flag at index 8.
        """
        inst_id = f"{symbol}-SWAP"
        params: dict = {"instId": inst_id, "bar": bar, "limit": str(limit)}
        if after is not None:
            params["after"] = str(after)
        if before is not None:
            params["before"] = str(before)
        data = await self._get("/api/v5/market/history-candles", params)
        if data.get("code") == "0" and data.get("data"):
            return data["data"]
        return []

    async def get_last_position_close(self, symbol: str, limit: int = 20) -> list:
        """Get recent closed positions. Returns list (newest first) with realizedPnl, closeAvgPx, openAvgPx, uTime."""
        inst_id = f"{symbol}-SWAP"
        data = await self._get(
            "/api/v5/account/positions-history",
            {"instId": inst_id, "limit": str(limit)},
        )
        if data.get("code") == "0" and data.get("data"):
            return data["data"]
        return []

    # --- Orders ---

    async def place_market_order(
        self, symbol: str, side: str, size: str, tp_price: str, sl_price: str
    ) -> Optional[dict]:
        """
        Place market order with OCO (TP + SL attached).
        side: 'buy' or 'sell'
        size: number of contracts
        """
        inst_id = f"{symbol}-SWAP"
        pos_side = "long" if side == "buy" else "short"

        body = {
            "instId": inst_id,
            "tdMode": "cross",
            "side": side,
            "ordType": "market",
            "sz": size,
            "attachAlgoOrds": [
                {
                    "tpTriggerPx": tp_price,
                    "tpOrdPx": "-1",  # market TP
                    "slTriggerPx": sl_price,
                    "slOrdPx": "-1",  # market SL
                }
            ],
        }

        logger.info(
            "Placing order | symbol={} side={} size={} tp={} sl={}",
            symbol, side, size, tp_price, sl_price
        )
        data = await self._post("/api/v5/trade/order", body)
        if data.get("code") == "0":
            order_id = data["data"][0].get("ordId")
            logger.info("Order placed | symbol={} side={} ordId={}", symbol, side, order_id)
            return data["data"][0]
        logger.error("Order failed | symbol={} side={} response={}", symbol, side, data)
        return None

    async def close_position(self, symbol: str, pos_side: str) -> bool:
        """Close position at market price."""
        inst_id = f"{symbol}-SWAP"
        body = {
            "instId": inst_id,
            "mgnMode": "cross",
        }
        logger.info("Closing position | symbol={} pos_side={}", symbol, pos_side)
        data = await self._post("/api/v5/trade/close-position", body)
        if data.get("code") == "0":
            logger.info("Position closed | symbol={} pos_side={}", symbol, pos_side)
            return True
        logger.error("Close failed | symbol={} response={}", symbol, data)
        return False

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage for symbol."""
        inst_id = f"{symbol}-SWAP"
        body = {
            "instId": inst_id,
            "lever": str(leverage),
            "mgnMode": "cross",
        }
        data = await self._post("/api/v5/account/set-leverage", body)
        if data.get("code") == "0":
            logger.info("Leverage set | symbol={} leverage={}x", symbol, leverage)
            return True
        logger.warning("Leverage set failed | symbol={} response={}", symbol, data)
        return False
