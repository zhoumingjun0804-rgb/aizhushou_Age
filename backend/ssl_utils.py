"""HTTPS 证书上下文与带代理的 HTTP 请求。"""
import os
import ssl
import urllib.request
from urllib.parse import urlparse

# 公司内网网关必须直连；走 HTTP_PROXY 易出现 SSL EOF / 504 / Cloudflare 1010
_DIRECT_HOST_SUFFIXES = (
    "61info.cn",
    "agenthub.vipthink.cn",
    "vipthink.cn",
    "gptproto.com",
)


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


def proxy_handlers_from_env() -> dict[str, str]:
    """合并系统代理与 .env 中的 HTTP(S)_PROXY；OPENAI_IMAGE_PROXY 优先用于 OpenAI。"""
    proxies = dict(urllib.request.getproxies())
    dedicated = os.environ.get("OPENAI_IMAGE_PROXY", "").strip()
    if dedicated:
        proxies["https"] = dedicated
        proxies["http"] = dedicated
        return proxies
    for scheme in ("http", "https"):
        for env_name in (f"{scheme.upper()}_PROXY", f"{scheme}_proxy"):
            value = os.environ.get(env_name, "").strip()
            if value:
                proxies[scheme] = value
    return proxies


def host_bypasses_proxy(hostname: str) -> bool:
    host = (hostname or "").lower().rstrip(".")
    if not host or host in ("127.0.0.1", "localhost"):
        return True
    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    for entry in (e.strip().lower() for e in no_proxy.split(",") if e.strip()):
        if entry.startswith(".") and host.endswith(entry):
            return True
        if host == entry or host.endswith(f".{entry}"):
            return True
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _DIRECT_HOST_SUFFIXES)


def should_use_proxy_for_url(url: str, use_proxy: bool) -> bool:
    if not use_proxy:
        return False
    host = urlparse(url).hostname or ""
    return not host_bypasses_proxy(host)


def open_http_request(
    req: urllib.request.Request,
    timeout: int,
    context: ssl.SSLContext | None = None,
    *,
    use_proxy: bool = True,
):
    """内网 61info.cn 等强制直连，避免 HTTP_PROXY 导致 SSL EOF。"""
    ctx = context or make_ssl_context()
    handlers: list = [urllib.request.HTTPSHandler(context=ctx)]
    if should_use_proxy_for_url(req.full_url, use_proxy):
        handlers.insert(0, urllib.request.ProxyHandler(proxy_handlers_from_env()))
    opener = urllib.request.build_opener(*handlers)
    return opener.open(req, timeout=timeout)
