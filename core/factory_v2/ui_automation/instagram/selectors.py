"""Known Instagram selectors used by fail-closed automation."""
from __future__ import annotations

from ..selectors import Selector

CONTINUE = Selector(
    semantic="continue",
    resource_ids=(
        "com.instagram.android:id/next_button",
        "com.instagram.android:id/continue_button",
    ),
    content_descs=("Next", "Continue", "Tiếp tục", "Tiếp"),
    texts=("Next", "Continue", "Tiếp tục", "Tiếp"),
    require_clickable=True,
)

# Keep the initial signup selector deliberately narrow. Generic "Sign up" text is
# also used by the final account-creation submit and must never be tapped from
# this selector without stronger screen context.
SIGN_UP = Selector(
    semantic="sign_up",
    content_descs=("Create new account", "Tạo tài khoản mới"),
    texts=("Create new account", "Tạo tài khoản mới"),
    require_clickable=True,
)

# Current Android 15 Instagram exposes the Accounts Center title as a regular
# view and the approved consent action as a separate clickable Button with only
# content-desc. Both are required together by the screen signature.
ACCOUNTS_CENTER_TITLE = Selector(
    semantic="accounts_center_title",
    text_contains_all=(
        "create a new instagram account",
        "accounts center",
        "allow the following",
    ),
)

ACCOUNTS_CENTER_ALLOW = Selector(
    semantic="accounts_center_allow",
    content_descs=("Allow and continue",),
    texts=("Allow and continue",),
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

BIRTH_DATE_INPUT = Selector(
    semantic="birth_date",
    resource_ids=(
        "com.instagram.android:id/birthday",
        "com.instagram.android:id/birthday_field",
        "com.instagram.android:id/date_of_birth",
    ),
)

ADD_PROFILE_PHOTO = Selector(
    semantic="add_profile_photo",
    resource_ids=(
        "com.instagram.android:id/add_profile_photo",
        "com.instagram.android:id/add_photo_button",
    ),
    content_descs=(
        "Add profile photo",
        "Add a profile picture",
        "Add picture",
        "Thêm ảnh đại diện",
    ),
    texts=(
        "Add profile photo",
        "Add a profile picture",
        "Add picture",
        "Thêm ảnh đại diện",
    ),
    require_clickable=True,
)

AVATAR_SKIP = Selector(
    semantic="avatar_skip",
    content_descs=("Skip", "Bỏ qua"),
    texts=("Skip", "Bỏ qua"),
    require_clickable=True,
)

FINAL_SIGNUP_SUBMIT = Selector(
    semantic="final_signup_submit",
    content_descs=("I agree",),
    texts=("Create account", "Sign up", "I agree", "Đăng ký", "Tạo tài khoản"),
    require_clickable=True,
)

CREATE_USERNAME_TITLE = Selector(
    semantic="create_username",
    content_descs=("Create a username", "Tạo tên người dùng"),
    texts=("Create a username", "Tạo tên người dùng"),
)

USERNAME_VALID_MARKER = Selector(
    semantic="username_valid",
    content_descs=("Input Username is valid.",),
    texts=("Input Username is valid.",),
    require_enabled=False,
)

USERNAME_UNAVAILABLE_MARKER = Selector(
    semantic="username_unavailable",
    text_contains_all=("username", "is not available"),
)

# Android 15 / current Instagram accessibility trees expose the signup username
# field without a stable resource-id. It is only used together with the
# CREATE_USERNAME_TITLE + CONTINUE screen signature, never as a global locator.
USERNAME_ENTRY_INPUT = Selector(
    semantic="username",
    class_names=("android.widget.EditText",),
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
    require_clickable=True,
)
ACCOUNT_SWITCHER = Selector(
    semantic="account_switcher",
    resource_ids=("com.instagram.android:id/action_bar_username_container",),
    content_descs=("Switch accounts", "Chuyển tài khoản"),
    texts=("Switch accounts", "Chuyển tài khoản"),
    require_clickable=True,
)
ADD_ACCOUNT = Selector(
    semantic="add_account",
    content_descs=(
        "Add Instagram account",
        "Add account",
        "Thêm tài khoản Instagram",
        "Thêm tài khoản",
    ),
    texts=(
        "Add Instagram account",
        "Add account",
        "Thêm tài khoản Instagram",
        "Thêm tài khoản",
    ),
    require_clickable=True,
)
