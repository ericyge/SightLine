"""Shared HTTPS settings for outbound provider calls (LLM, speech-to-text)."""
try:  # Use the OS certificate store: antivirus/VPN/corporate proxies re-sign HTTPS on many laptops
    import ssl

    import truststore
    VERIFY: "ssl.SSLContext | bool" = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
except ImportError:  # falls back to httpx's default certificate bundle
    VERIFY = True
