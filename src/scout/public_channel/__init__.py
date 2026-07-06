"""Public news-channel surface for Scout.

This package is deliberately separate from the trading scanner. It can reuse
public source adapters, but it must not emit trade instructions or touch order
surfaces.
"""

