"""Known Instagram selectors used by fail-closed automation."""
from __future__ import annotations

from ..selectors import Selector

CONTINUE = Selector(
    semantic="continue",
    resource_ids=(
        "com.instagram.android:id/next_button",
        "com.instagram.android:id/continue_button",
    ),
    texts=("Next", "Continue", "Tiếp tục", "Tiếp"),
    require_clickable=True,
)

# Keep the initial signup selector deliberately narrow. Generic "Sign up" text is
# also used by the final account-creation submit and must never be tapped from
# this selector without stronger screen context.
SIGN_UP = Selector(
    semantic="sign_up",
    texts=("Create new account", "Tạo tài khoản mới"),
    require_clickable=True,
)

SIGNUP_CONTACT_INPUT = Selector(
    semantic="signup_contact",
    resource_ids=(
        "com.instagram.android:id/email_or_phone",
        "com.instagram.android:id/email_or_phone_input",
        "com.instagram.android:id/contact_point",
    ),
)

FINAL_SIGNUP_SUBMIT = Selector(
    semantic="final_signup_submit",
    texts=("Create account", "Sign up", "Đăng ký", "Tạo tài khoản"),
    require_clickable=True,
)

USERNAME_INPUT = Selector(
    semantic="username",
    resource_ids=(
        "com.instagram.android:id/username",
        "com.instagram.android:id/username_field",
    ),
)
DISPLAY_NAME_INPUT = Selector(
    semantic="display_name",
    resource_ids=(
        "com.instagram.android:id/full_name",
        "com.instagram.android:id/name",
    ),
)
BIO_INPUT = Selector(
    semantic="bio",
    resource_ids=(
        "com.instagram.android:id/bio",
        "com.instagram.android:id/bio_field",
    ),
    texts=("Bio", "Tiểu sử"),
)
HOME = Selector(
    semantic="home",
    content_descs=("Home", "Trang chủ"),
    resource_ids=("com.instagram.android:id/feed_tab",),
)
PROFILE = Selector(
    semantic="profile",
    content_descs=("Profile", "Trang cá nhân"),
    resource_ids=("com.instagram.android:id/profile_tab",),
)
