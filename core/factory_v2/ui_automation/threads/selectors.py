"""Known Threads selectors used by fail-closed automation."""
from __future__ import annotations

from ..selectors import Selector

CONTINUE = Selector(
    semantic="continue",
    texts=("Continue", "Next", "Tiếp tục", "Tiếp"),
    require_clickable=True,
)
CONTINUE_WITH_INSTAGRAM = Selector(
    semantic="continue_with_instagram",
    texts=("Continue with Instagram", "Import from Instagram"),
    require_clickable=True,
)
JOIN_THREADS = Selector(
    semantic="join_threads",
    texts=("Join Threads", "Tham gia Threads"),
    require_clickable=True,
)
THREADS_TERMS_MARKER = Selector(
    semantic="threads_terms_marker",
    text_contains_all=("threads", "terms"),
    require_enabled=False,
)
ACCOUNT_PICKER_MARKER = Selector(
    semantic="threads_account_picker_marker",
    texts=("Log into Threads",),
    require_enabled=False,
)
OTHER_ACCOUNTS = Selector(
    semantic="threads_other_accounts",
    texts=("2 others", "1 other", "More accounts"),
    require_clickable=True,
)
NOTIFICATION_PROMPT = Selector(
    semantic="threads_notification_prompt",
    text_contains_all=("allow threads", "notifications"),
    require_enabled=False,
)
NOTIFICATION_DENY = Selector(
    semantic="threads_notification_deny",
    texts=("Don’t allow", "Don't allow", "Không cho phép"),
    require_clickable=True,
)
FOLLOW_SUGGESTIONS_MARKER = Selector(
    semantic="threads_follow_suggestions_marker",
    text_contains_all=("follow suggestions", "instagram activity"),
    require_enabled=False,
)
FOLLOW_SUGGESTIONS_CLOSE = Selector(
    semantic="threads_follow_suggestions_close",
    content_descs=("Close", "Đóng"),
    texts=("Close", "Đóng"),
    require_clickable=True,
)
DISPLAY_NAME_INPUT = Selector(
    semantic="display_name",
    resource_ids=("com.instagram.barcelona:id/name", "com.instagram.barcelona:id/full_name"),
)
BIO_INPUT = Selector(
    semantic="bio",
    resource_ids=("com.instagram.barcelona:id/bio", "com.instagram.barcelona:id/bio_field"),
    texts=("Bio", "Tiểu sử"),
)
HOME = Selector(
    semantic="home",
    content_descs=("Home", "Trang chủ"),
    resource_ids=(
        "MainFeedScreen",
        "com.instagram.barcelona:id/barcelona_tab_main_feed",
        "com.instagram.barcelona:id/home_tab",
    ),
)
PROFILE = Selector(
    semantic="profile",
    content_descs=("Profile", "Trang cá nhân"),
    resource_ids=(
        "com.instagram.barcelona:id/barcelona_tab_profile",
        "com.instagram.barcelona:id/profile_tab",
    ),
)
