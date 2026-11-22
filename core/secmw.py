"""Security headers for every response. The page needs inline scripts and styles, so those are allowed; everything else is locked to known sources."""
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://checkout.razorpay.com; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob: https:; "
       "connect-src 'self' https://api.open-meteo.com https://geocoding-api.open-meteo.com https://api.bigdatacloud.net https://ipwho.is https://api.razorpay.com https://lumberjack.razorpay.com; "
       "frame-src https://api.razorpay.com https://checkout.razorpay.com; worker-src 'self'; manifest-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'")


class SecurityHeaders:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        r = self.get_response(request)
        r.headers.setdefault('Content-Security-Policy', CSP)
        r.headers.setdefault('Permissions-Policy', 'geolocation=(self), camera=(self), microphone=(), usb=(), payment=(self "https://api.razorpay.com" "https://checkout.razorpay.com")')
        r.headers.setdefault('Referrer-Policy', 'same-origin')
        p = request.path
        if p.startswith('/api/') or p.startswith('/billing/') or p.startswith('/owner') or p.startswith('/device/') or p.startswith('/iclock/'):
            r.headers['Cache-Control'] = 'no-store'
        return r


_HASHES = {}


def page_csp(html_bytes):
    """CSP for the app page: only our own inline script (by its hash) may run, so injected HTML can never run script."""
    import base64
    import hashlib
    import re
    k = hash(html_bytes)
    if k not in _HASHES:
        hs = []
        for m in re.finditer(rb'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html_bytes, re.S):
            hs.append("'sha256-%s'" % base64.b64encode(hashlib.sha256(m.group(1)).digest()).decode())
        _HASHES.clear()
        _HASHES[k] = CSP.replace("script-src 'self' 'unsafe-inline'", "script-src 'self' " + ' '.join(hs))
    return _HASHES[k]
