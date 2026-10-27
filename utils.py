from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config


def compute_publish_after() -> str:
    """Requirement 2: publish 09:00-22:00, otherwise the next day at 09:00."""
    now = datetime.now(ZoneInfo(config.TIMEZONE))
    if config.PUBLISH_START_HOUR <= now.hour < config.PUBLISH_END_HOUR:
        target = now
    elif now.hour < config.PUBLISH_START_HOUR:
        target = now.replace(hour=config.PUBLISH_START_HOUR, minute=0, second=0, microsecond=0)
    else:
        target = (now + timedelta(days=1)).replace(
            hour=config.PUBLISH_START_HOUR, minute=0, second=0, microsecond=0
        )
    return target.isoformat()