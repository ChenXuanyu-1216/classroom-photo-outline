"""Safe transport diagnostics without credentials, payloads or response text."""
import logging
import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from app.config import settings

logger = logging.getLogger(__name__)


def retry_allowed(attempt, limit):
    # Cloud calls with an unknown provider outcome require an explicit retry.
    return settings.deployment_mode == 'local' and attempt < limit


def wait_before_retry(attempt: int, response=None) -> bool:
    """Bound retries; never retry earlier than a valid server Retry-After."""
    delay = min(.5 * (2 ** min(attempt, 4)), 5.0)
    header = response.headers.get('Retry-After') if response is not None else None
    if header:
        try:
            requested = float(header)
            if not math.isfinite(requested) or requested < 0:
                raise ValueError('Invalid delay')
        except ValueError:
            try:
                date = parsedate_to_datetime(header)
                if date.tzinfo is None:
                    raise ValueError('Missing timezone')
                requested = max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                requested = 0.0
        if requested > 5:
            return False
        delay = max(delay, requested)
    time.sleep(delay)
    return True


def report_transport_failure(stage, error):
    causes, seen = [], set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        code = getattr(error, 'winerror', None) or getattr(error, 'errno', None)
        causes.append(type(error).__name__ + (f'[{code}]' if isinstance(code, int) else ''))
        error = error.__cause__
    logger.warning('Model connection failed: stage=%s route=%s causes=%s', stage,
                   'environment' if settings.model_trust_env else 'direct', ' -> '.join(causes))
