"""
Shared slowapi Limiter instance. Lives in its own module (not api/app.py)
specifically so route modules can `from api.rate_limit import limiter` and
apply `@limiter.limit(...)` without a circular import — api/app.py imports
every route module's router, so a route module importing back from api/app
would cycle.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
