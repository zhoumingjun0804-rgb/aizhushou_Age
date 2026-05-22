"""HTTPS 证书上下文：macOS 自带 Python 缺根证书时用 certifi 兜底。"""
import os
import ssl


def make_ssl_context() -> ssl.SSLContext:
    if os.environ.get("LOVART_INSECURE_SSL") == "1" or os.environ.get("INSECURE_SSL") == "1":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()
