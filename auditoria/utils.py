# auditoria/utils.py
import threading

__local = threading.local()

def set_current_request(request):
    __local.request = request

def get_current_request():
    return getattr(__local, "request", None)

def get_current_user():
    req = get_current_request()
    if req and hasattr(req, "user") and req.user.is_authenticated:
        return req.user
    return None

def get_current_ip():
    req = get_current_request()
    if not req:
        return None
    ip = req.META.get("HTTP_X_FORWARDED_FOR")
    if ip:
        ip = ip.split(",")[0].strip()  # primeiro IP
    else:
        ip = req.META.get("REMOTE_ADDR")
    return ip
