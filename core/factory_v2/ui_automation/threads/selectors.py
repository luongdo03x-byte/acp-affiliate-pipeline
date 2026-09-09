"""Known Threads selectors used by fail-closed automation."""
from __future__ import annotations

from ..selectors import Selector

CONTINUE = Selector(semantic="continue", texts=("Continue", "Next", "Tiếp tục", "Tiếp"), require_clickable=True)
JOIN_THREADS = Selector(semantic="join_threads", texts=("Join Threads", "Continue with Instagram", "Import from Instagram", "Tham gia Threads"), require_clickable=True)
DISPLAY_NAME_INPUT = Selector(semantic="display_name", resource_ids=("com.instagram.barcelona:id/name", "com.instagram.barcelona:id/full_name"))
BIO_INPUT = Selector(semantic="bio", resource_ids=("com.instagram.barcelona:id/bio", "com.instagram.barcelona:id/bio_field"), texts=("Bio", "Tiểu sử"))
HOME = Selector(semantic="home", content_descs=("Home", "Trang chủ"), resource_ids=("com.instagram.barcelona:id/home_tab",))
PROFILE = Selector(semantic="profile", content_descs=("Profile", "Trang cá nhân"), resource_ids=("com.instagram.barcelona:id/profile_tab",))
SETTINGS_ENTRY = Selector(semantic="settings", texts=("Settings", "Cài đặt"))
ACCOUNT_ENTRY = Selector(semantic="account", texts=("Account", "Tài khoản"), require_clickable=True)
WEBSITE_PERMISSIONS = Selector(semantic="website_permissions", texts=("Website permissions", "Quyền trang web"), require_clickable=True)
TESTER_INVITES = Selector(semantic="tester_invites", texts=("Invites", "Lời mời"), require_clickable=True)
TESTER_ACCEPT = Selector(semantic="tester_accept", texts=("Accept", "Chấp nhận"), require_clickable=True)
OAUTH_CONSENT_MARKER = Selector(semantic="oauth_consent", texts=("threads_basic", "threads_content_publish", "Profile info and posts"))
OAUTH_CONSENT_ALLOW = Selector(semantic="oauth_allow", texts=("Allow", "Cho phép", "Authorize", "Ủy quyền"), require_clickable=True)
