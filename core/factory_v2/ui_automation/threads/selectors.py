"""Known Threads selectors used by fail-closed automation."""
from __future__ import annotations

from ..selectors import Selector

CONTINUE = Selector(semantic="continue", texts=("Continue", "Next", "Tiếp tục", "Tiếp"), require_clickable=True)
JOIN_THREADS = Selector(semantic="join_threads", texts=("Join Threads", "Continue with Instagram", "Import from Instagram", "Tham gia Threads"), require_clickable=True)
DISPLAY_NAME_INPUT = Selector(semantic="display_name", resource_ids=("com.instagram.barcelona:id/name", "com.instagram.barcelona:id/full_name"))
BIO_INPUT = Selector(semantic="bio", resource_ids=("com.instagram.barcelona:id/bio", "com.instagram.barcelona:id/bio_field"), texts=("Bio", "Tiểu sử"))
HOME = Selector(semantic="home", content_descs=("Home", "Trang chủ"), resource_ids=("com.instagram.barcelona:id/home_tab",))
PROFILE = Selector(semantic="profile", content_descs=("Profile", "Trang cá nhân"), resource_ids=("com.instagram.barcelona:id/profile_tab",))
