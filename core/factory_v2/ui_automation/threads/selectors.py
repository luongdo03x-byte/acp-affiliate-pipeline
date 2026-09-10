"""Known Threads selectors used by fail-closed automation."""
from __future__ import annotations

from ..selectors import Selector

CONTINUE = Selector(semantic="continue", texts=("Continue", "Next", "Tiếp tục", "Tiếp"), require_clickable=True)
JOIN_THREADS = Selector(semantic="join_threads", texts=("Join Threads", "Continue with Instagram", "Import from Instagram", "Tham gia Threads"), require_clickable=True)
DISPLAY_NAME_INPUT = Selector(semantic="display_name", resource_ids=("com.instagram.barcelona:id/name", "com.instagram.barcelona:id/full_name"))
BIO_INPUT = Selector(semantic="bio", resource_ids=("com.instagram.barcelona:id/bio", "com.instagram.barcelona:id/bio_field"), texts=("Bio", "Tiểu sử"))
HOME = Selector(semantic="home", content_descs=("Home", "Trang chủ"), resource_ids=("com.instagram.barcelona:id/home_tab",))
PROFILE = Selector(semantic="profile", content_descs=("Profile", "Trang cá nhân"), resource_ids=("com.instagram.barcelona:id/profile_tab", "com.instagram.barcelona:id/barcelona_tab_profile", "barcelona_tab_profile"))
# Đọc trực tiếp từ cây giao diện Threads trên máy thật ngày 10/09. Nhãn đúng là
# "Quyền trên trang web", và nút Cài đặt trên trang cá nhân là icon chỉ có
# content-desc chứ không có text.
PROFILE_SETTINGS_ICON = Selector(
    semantic="profile_settings",
    resource_ids=(
        "com.instagram.barcelona:id/profile_screen_profile_settings",
        "profile_screen_profile_settings",
    ),
    content_descs=("Settings", "Cài đặt"),
)
WEBSITE_PERMISSIONS = Selector(
    semantic="website_permissions",
    texts=("Website permissions", "Quyền trên trang web"),
    content_descs=("Website permissions", "Quyền trên trang web"),
)
APPS_AND_WEBSITES = Selector(
    semantic="apps_and_websites",
    resource_ids=("com.instagram.barcelona:id/action_bar_title",),
    texts=("Apps and Websites", "Ứng dụng và trang web"),
    content_descs=("Apps and Websites", "Ứng dụng và trang web"),
)
TESTER_INVITES = Selector(
    semantic="tester_invites",
    texts=("Invites", "Lời mời"),
    content_descs=("Invites", "Lời mời"),
)
# Nút thật là Button chỉ có content-desc, và "Từ chối" nằm ngay bên dưới, nên
# selector phải khớp đúng chữ.
TESTER_ACCEPT = Selector(
    semantic="tester_accept",
    texts=("Accept", "Chấp nhận"),
    content_descs=("Accept", "Chấp nhận"),
)
INVITES_TAB_HINT = Selector(
    semantic="invites_hint",
    texts=("Đây là những ứng dụng và trang web mà bạn đã được mời thử nghiệm.",),
)
OAUTH_CONSENT_MARKER = Selector(semantic="oauth_consent", texts=("threads_basic", "threads_content_publish", "Profile info and posts"))
OAUTH_CONSENT_ALLOW = Selector(semantic="oauth_allow", texts=("Allow", "Cho phép", "Authorize", "Ủy quyền"), require_clickable=True)
