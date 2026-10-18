"""Shared HTTPS settings for outbound provider calls (LLM, speech-to-text)."""
try:  # Use the OS certificate store: antivirus/VPN/corporate proxies re-sign HTTPS on many laptops
    import ssl

    import truststore
    VERIFY: "ssl.SSLContext | bool" = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
except ImportError:  # falls back to httpx's default certificate bundle
    VERIFY = True


def use_system_certificates() -> None:
    """Make the Perceptron SDK's HTTP client trust the OS certificate store (antivirus/VPN/proxies re-sign
    HTTPS on many laptops, which breaks Python's bundled certificates; seen as CERTIFICATE_VERIFY_FAILED).
    The SDK looks `_http_client` up at call time precisely so it can be replaced. Idempotent, and leaves a
    test's mock in place. Used by both live vision clients."""
    import httpx
    from perceptron import client as sdk
    if getattr(sdk._http_client, "__qualname__", "") != "_http_client":
        return   # already replaced (by us, or by a test)

    def _system_cert_http_client(timeout):
        return httpx.Client(timeout=timeout, verify=VERIFY)

    sdk._http_client = _system_cert_http_client
