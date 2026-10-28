"""Block quantisation formats in the style of llama.cpp / GGUF (blocks of 32 weights, fp16 scale), implemented in NumPy.

Q8_0, Q5_0, Q4_0, Q4_1 follow llama.cpp's published layouts and byte counts (34, 22, 18, 20 bytes per 32 weights = 8.5, 5.5, 4.5,
5.0 bits per weight). Q5_1 follows the same pattern. Q3S and Q2S are plain symmetric 3-/2-bit variants defined here (llama.cpp's
real Q3_K/Q2_K are super-block formats that are NOT implemented). No GGUF files are read or written.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

BLOCK = 32


@dataclass(frozen=True)
class Packed:
    fmt: str
    shape: tuple[int, ...]
    scales: np.ndarray  # float16, one per block
    mins: np.ndarray | None  # float16 (asymmetric formats)
    payload: np.ndarray  # uint8, bit-packed integer codes
    pad: int

    @property
    def nbytes(self) -> int:
        return (
            self.scales.nbytes
            + (self.mins.nbytes if self.mins is not None else 0)
            + self.payload.nbytes
        )


# (bits, symmetric?) per format
FORMATS = {
    "Q8_0": (8, True),
    "Q5_0": (5, True),
    "Q5_1": (5, False),
    "Q4_0": (4, True),
    "Q4_1": (4, False),
    "Q3S": (3, True),
    "Q2S": (2, True),
}


def bits_per_weight(fmt: str) -> float:
    bits, sym = FORMATS[fmt]
    return (16 + bits * BLOCK + (0 if sym else 16)) / BLOCK


def _blocks(x: np.ndarray) -> tuple[np.ndarray, int]:
    flat = x.astype(np.float32).ravel()
    pad = (-len(flat)) % BLOCK
    return np.concatenate([flat, np.zeros(pad, np.float32)]).reshape(-1, BLOCK), pad


def pack_bits(codes: np.ndarray, bits: int) -> np.ndarray:
    """Pack unsigned integer codes (each < 2**bits) into bytes, little-endian bit order."""
    c = codes.astype(np.uint8).ravel()
    planes = ((c[:, None] >> np.arange(bits, dtype=np.uint8)) & 1).astype(np.uint8).ravel()
    return np.packbits(planes, bitorder="little")


def unpack_bits(payload: np.ndarray, bits: int, n: int) -> np.ndarray:
    planes = np.unpackbits(payload, bitorder="little")[: n * bits].reshape(n, bits)
    return (planes << np.arange(bits, dtype=np.uint8)).sum(1).astype(np.uint8)


def quantise(x: np.ndarray, fmt: str) -> Packed:
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt}")
    if not np.isfinite(x).all():
        raise ValueError("weights contain NaN or inf")
    bits, sym = FORMATS[fmt]
    blk, pad = _blocks(x)
    levels = 2**bits - 1
    mins = None
    if sym:
        # llama.cpp: d = (signed value of largest magnitude) / -half; code = clamp(round(x/d) + half, 0, levels)
        half = 2 ** (bits - 1)
        idx = np.abs(blk).argmax(1)
        peak = blk[np.arange(len(blk)), idx]
        qmax = half - 1 if fmt == "Q8_0" else half  # Q8_0 uses +-127 around zero with signed codes
        d = (peak / -qmax if fmt != "Q8_0" else np.abs(peak) / 127).astype(np.float16)
        dd = d.astype(np.float32)
        inv = np.where(dd != 0, 1.0 / np.where(dd == 0, 1, dd), 0.0)
        codes = (
            np.clip(np.rint(blk * inv[:, None]) + half, 0, levels)
            if fmt != "Q8_0"
            else np.clip(np.rint(blk * inv[:, None]), -127, 127) + 128
        )
        scales = d
    else:
        lo, hi = blk.min(1), blk.max(1)
        d = ((hi - lo) / levels).astype(np.float16)
        dd = d.astype(np.float32)
        inv = np.where(dd != 0, 1.0 / np.where(dd == 0, 1, dd), 0.0)
        mins = lo.astype(np.float16)
        codes = np.clip(np.rint((blk - mins.astype(np.float32)[:, None]) * inv[:, None]), 0, levels)
        scales = d
    return Packed(fmt, tuple(x.shape), scales, mins, pack_bits(codes.astype(np.uint8), bits), pad)


def dequantise(p: Packed) -> np.ndarray:
    bits, sym = FORMATS[p.fmt]
    n = len(p.scales) * BLOCK
    codes = unpack_bits(p.payload, bits, n).reshape(-1, BLOCK).astype(np.float32)
    d = p.scales.astype(np.float32)[:, None]
    if sym:
        center = 128 if p.fmt == "Q8_0" else 2 ** (bits - 1)
        out = (codes - center) * d
    else:
        out = codes * d + p.mins.astype(np.float32)[:, None]
    flat = out.ravel()
    return flat[: len(flat) - p.pad].reshape(p.shape)


def sqnr_db(x: np.ndarray, y: np.ndarray) -> float:
    noise = float(((x - y) ** 2).sum())
    return float("inf") if noise == 0 else float(10 * np.log10((x**2).sum() / noise))
