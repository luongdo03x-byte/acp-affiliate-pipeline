package com.acp.accountfactory.runner

import java.text.Normalizer
import java.util.Locale

data class LocalUiNode(
    val text: String = "",
    val contentDescription: String = "",
    val viewId: String = "",
    val className: String = "",
    val clickable: Boolean = false,
    val longClickable: Boolean = false,
    val editable: Boolean = false,
    val password: Boolean = false,
    val left: Int = 0,
    val top: Int = 0,
    val right: Int = 0,
    val bottom: Int = 0,
)

data class LocalUiSelector(
    val resourceIds: Set<String> = emptySet(),
    val texts: Set<String> = emptySet(),
    val contentDescriptions: Set<String> = emptySet(),
    val contentDescriptionPrefixes: Set<String> = emptySet(),
    val requireClickable: Boolean = false,
    val requireLongClickable: Boolean = false,
    val requireEditable: Boolean = false,
)

interface LocalAccessibilityBridge {
    fun foregroundPackage(): String?
    fun nodes(): List<LocalUiNode>
    fun click(selector: LocalUiSelector): Boolean
    fun longClick(selector: LocalUiSelector): Boolean
    fun tapAt(x: Int, y: Int): Boolean
    fun dismissKeyboard(): Boolean
    fun setText(selector: LocalUiSelector, value: String): Boolean
}

data class LocalFlowOutcome(
    val status: String,
    val screen: String,
    val reason: String? = null,
    val actualUsername: String? = null,
) {
    fun result(): Map<String, Any?> = buildMap {
        put("flow_status", status)
        put("screen", screen)
        put("reason", reason)
        actualUsername?.let { put("actual_username", it) }
    }
}

/** Fail-closed Accessibility automation shared by physical-device commands. */
class LocalSafeUiAutomation(private val bridge: LocalAccessibilityBridge) {
    fun foregroundPackageForRunner(): String? = bridge.foregroundPackage()

    private val continueSelector = LocalUiSelector(
        resourceIds = setOf(
            "com.instagram.android:id/next_button",
            "com.instagram.android:id/continue_button",
        ),
        texts = setOf("Next", "Continue", "Tiếp tục", "Tiếp"),
        contentDescriptions = setOf("Next", "Continue", "Tiếp tục", "Tiếp"),
        requireClickable = true,
    )
    private val instagramSignup = LocalUiSelector(
        texts = setOf("Create new account", "Tạo tài khoản mới"),
        contentDescriptions = setOf("Create new account", "Tạo tài khoản mới"),
        requireClickable = true,
    )
    private val accountCenterConsent = LocalUiSelector(
        texts = setOf("Allow and continue", "Cho phép và tiếp tục"),
        contentDescriptions = setOf("Allow and continue", "Cho phép và tiếp tục"),
        requireClickable = true,
    )
    private val instagramTermsConsent = LocalUiSelector(
        texts = setOf("I agree", "Tôi đồng ý"),
        contentDescriptions = setOf("I agree", "Tôi đồng ý"),
        requireClickable = true,
    )
    private val instagramProfileTab = LocalUiSelector(
        resourceIds = setOf("com.instagram.android:id/profile_tab"),
        contentDescriptions = setOf("Profile", "Trang cá nhân"),
        requireLongClickable = true,
    )
    private val instagramAddAccount = LocalUiSelector(
        texts = setOf(
            "Add Instagram account", "Add account",
            "Thêm tài khoản Instagram", "Thêm tài khoản",
        ),
        contentDescriptions = setOf(
            "Add Instagram account", "Add account",
            "Thêm tài khoản Instagram", "Thêm tài khoản",
        ),
        requireClickable = true,
    )
    private val contactInput = LocalUiSelector(
        resourceIds = setOf(
            "com.instagram.android:id/email_or_phone",
            "com.instagram.android:id/email_or_phone_input",
            "com.instagram.android:id/contact_point",
        ),
        requireEditable = true,
    )
    private val birthdayInput = LocalUiSelector(
        resourceIds = setOf(
            "com.instagram.android:id/birthday",
            "com.instagram.android:id/birthday_field",
            "com.instagram.android:id/date_of_birth",
        ),
        requireEditable = true,
    )
    private val usernameInput = LocalUiSelector(
        resourceIds = setOf(
            "com.instagram.android:id/username",
            "com.instagram.android:id/username_field",
        ),
        contentDescriptionPrefixes = setOf("Username", "Tên người dùng"),
        requireEditable = true,
    )
    private val instagramNameInput = LocalUiSelector(
        resourceIds = setOf(
            "com.instagram.android:id/full_name",
            "com.instagram.android:id/name",
        ),
        requireEditable = true,
    )
    private val instagramBioInput = LocalUiSelector(
        resourceIds = setOf(
            "com.instagram.android:id/bio",
            "com.instagram.android:id/bio_field",
        ),
        requireEditable = true,
    )
    private val threadsJoin = LocalUiSelector(
        texts = setOf(
            "Join Threads", "Continue with Instagram", "Import from Instagram",
            "Tham gia Threads", "Tiếp tục bằng Instagram", "Nhập từ Instagram",
        ),
        contentDescriptions = setOf(
            "Join Threads", "Continue with Instagram", "Import from Instagram",
            "Tham gia Threads", "Tiếp tục bằng Instagram", "Nhập từ Instagram",
        ),
        requireClickable = true,
    )
    private val threadsProfileTab = LocalUiSelector(
        resourceIds = setOf(
            "com.instagram.barcelona:id/profile_tab",
            "com.instagram.barcelona:id/barcelona_tab_profile",
            "barcelona_tab_profile",
        ),
        contentDescriptions = setOf("Profile", "Trang cá nhân"),
    )
    private val threadsAddProfile = LocalUiSelector(
        texts = setOf(
            "Add profile", "Add account",
            "Thêm trang cá nhân", "Thêm tài khoản",
        ),
        contentDescriptions = setOf(
            "Add profile", "Add account",
            "Thêm trang cá nhân", "Thêm tài khoản",
        ),
        requireClickable = true,
    )
    private val threadsNameInput = LocalUiSelector(
        resourceIds = setOf(
            "com.instagram.barcelona:id/name",
            "com.instagram.barcelona:id/full_name",
        ),
        requireEditable = true,
    )
    private val threadsBioInput = LocalUiSelector(
        resourceIds = setOf(
            "com.instagram.barcelona:id/bio",
            "com.instagram.barcelona:id/bio_field",
        ),
        requireEditable = true,
    )

    fun runInstagram(profile: Map<String, String?>): LocalFlowOutcome {
        val nodes = bridge.nodes()
        val screen = detectInstagram(nodes)
        protectedOutcome(screen)?.let { return it }
        when (screen) {
            "IG_HOME" -> return act(screen, bridge.longClick(instagramProfileTab))
            "IG_ACCOUNT_SWITCHER" -> return act(screen, bridge.click(instagramAddAccount))
            "IG_SIGNUP_ENTRY" -> return act(screen, bridge.click(instagramSignup))
            "IG_ACCOUNT_CENTER_CONSENT" ->
                return act(screen, bridge.click(accountCenterConsent))
            "IG_TERMS_CONSENT" ->
                return act(screen, bridge.click(instagramTermsConsent))
            "IG_CONTACT_ENTRY" -> {
                val contact = profile["signup_contact"].orEmpty().trim()
                if (contact.isEmpty()) return confirmation(screen, "MISSING_SIGNUP_CONTACT")
                if (!bridge.setText(contactInput, contact)) return confirmation(screen)
                return act(screen, bridge.click(continueSelector))
            }
            "IG_BIRTHDAY_ENTRY" -> {
                val birthDate = profile["birth_date"].orEmpty().trim()
                if (birthDate.isEmpty()) return confirmation(screen, "MISSING_BIRTH_DATE")
                if (!bridge.setText(birthdayInput, birthDate)) return confirmation(screen)
                return act(screen, bridge.click(continueSelector))
            }
            "IG_PROFILE_SETUP" -> {
                // Instagram từ chối tên và ghi RÕ TÊN ĐÓ trong thông báo. Nếu không
                // nhận ra, runner gõ lại tên cũ mỗi vòng lặp; mỗi lần gõ lại khởi
                // động lại quá trình kiểm tra tên nên nó chạy vô hạn, vừa không tiến
                // được vừa nện liên tục vào Instagram.
                //
                // Chỉ dừng khi thông báo nhắc đúng tên ĐANG định điền -- thông báo
                // còn sót của tên trước đó không được chặn lượt điền tên mới.
                val wantedUsername = profile["username"]
                val availableUsername = availableUsername(nodes)
                if (!wantedUsername.isNullOrBlank() && availableUsername != null) {
                    val adoptedUsername = availableUsername.takeUnless {
                        it.equals(wantedUsername, ignoreCase = true)
                    }
                    if (has(nodes, continueSelector)) {
                        return if (bridge.click(continueSelector)) {
                            LocalFlowOutcome(
                                "running", screen, actualUsername = adoptedUsername,
                            )
                        } else {
                            confirmation(screen)
                        }
                    }
                    return if (bridge.dismissKeyboard()) {
                        LocalFlowOutcome(
                            "running", screen, "KEYBOARD_DISMISSED",
                            actualUsername = adoptedUsername,
                        )
                    } else {
                        LocalFlowOutcome(
                            "needs_confirmation", screen, "UI_CHANGED",
                            actualUsername = adoptedUsername,
                        )
                    }
                }
                if (!wantedUsername.isNullOrBlank() && usernameRejected(nodes, wantedUsername)) {
                    if (chooseUsernameSuggestion(nodes, wantedUsername)) {
                        return LocalFlowOutcome("running", screen, "USERNAME_SUGGESTION_SELECTED")
                    }
                    return LocalFlowOutcome("retry_pending", screen, "USERNAME_UNAVAILABLE")
                }
                val fields = listOf(
                    usernameInput to profile["username"],
                    instagramNameInput to profile["display_name"],
                    instagramBioInput to profile["bio"],
                )
                var changed = false
                fields.forEach { (selector, value) ->
                    if (!value.isNullOrBlank() && has(nodes, selector)) {
                        if (!bridge.setText(selector, value)) return confirmation(screen)
                        changed = true
                    }
                }
                if (has(bridge.nodes(), continueSelector)) {
                    return act(screen, bridge.click(continueSelector))
                }
                return if (changed) LocalFlowOutcome("running", screen) else confirmation(screen)
            }
            "IG_AVATAR_SETUP" -> return confirmation(screen, "AVATAR_REQUIRES_OPERATOR")
            "RATE_LIMITED", "ACTION_BLOCKED", "NETWORK_ERROR" ->
                return LocalFlowOutcome("retry_pending", screen, screen)
            "ACCOUNT_DISABLED" -> return LocalFlowOutcome("error", screen, screen)
            else -> return confirmation(screen)
        }
    }

    fun runThreads(profile: Map<String, String?>): LocalFlowOutcome {
        val nodes = bridge.nodes()
        val screen = detectThreads(nodes)
        protectedOutcome(screen)?.let { return it }
        when (screen) {
            "THREADS_HOME" -> return act(screen, bridge.longClick(threadsProfileTab))
            "THREADS_ACCOUNT_SWITCHER" -> return act(screen, bridge.click(threadsAddProfile))
            "THREADS_ONBOARDING" -> {
                val selector = if (has(nodes, threadsJoin)) threadsJoin else continueSelector
                return act(screen, bridge.click(selector))
            }
            "THREADS_PROFILE_SETUP" -> {
                val fields = listOf(
                    threadsNameInput to profile["display_name"],
                    threadsBioInput to profile["bio"],
                )
                var changed = false
                fields.forEach { (selector, value) ->
                    if (!value.isNullOrBlank() && has(nodes, selector)) {
                        if (!bridge.setText(selector, value)) return confirmation(screen)
                        changed = true
                    }
                }
                if (has(bridge.nodes(), continueSelector)) {
                    return act(screen, bridge.click(continueSelector))
                }
                return if (changed) LocalFlowOutcome("running", screen) else confirmation(screen)
            }
            "RATE_LIMITED", "ACTION_BLOCKED", "NETWORK_ERROR" ->
                return LocalFlowOutcome("retry_pending", screen, screen)
            "ACCOUNT_DISABLED" -> return LocalFlowOutcome("error", screen, screen)
            else -> return confirmation(screen)
        }
    }

    fun observe(flow: String): LocalFlowOutcome {
        val nodes = bridge.nodes()
        val screen = if (flow.lowercase(Locale.ROOT) == "threads") {
            detectThreads(nodes)
        } else {
            detectInstagram(nodes)
        }
        protectedOutcome(screen)?.let { return it }
        val safeSuccessor = screen in if (flow.lowercase(Locale.ROOT) == "threads") {
            setOf("THREADS_PROFILE_SETUP")
        } else {
            setOf("IG_PROFILE_SETUP", "IG_AVATAR_SETUP")
        }
        if (safeSuccessor) return LocalFlowOutcome("completed", screen)
        if (screen in setOf("RATE_LIMITED", "ACTION_BLOCKED", "NETWORK_ERROR")) {
            return LocalFlowOutcome("retry_pending", screen, screen)
        }
        if (screen == "ACCOUNT_DISABLED") return LocalFlowOutcome("error", screen, screen)
        return confirmation(screen, if (screen == "UNKNOWN") "UI_CHANGED" else "CHECKPOINT_NOT_CONFIRMED")
    }

    private fun detectInstagram(nodes: List<LocalUiNode>): String {
        if (bridge.foregroundPackage() != INSTAGRAM_PACKAGE) return "UNKNOWN"
        detectCommon(nodes)?.let { return it }
        if (has(nodes, instagramAddAccount)) return "IG_ACCOUNT_SWITCHER"
        if (has(nodes, instagramSignup)) return "IG_SIGNUP_ENTRY"
        if (has(nodes, accountCenterConsent) &&
            containsAny(nodes, "account center", "trung tam tai khoan")
        ) return "IG_ACCOUNT_CENTER_CONSENT"
        if (has(nodes, instagramTermsConsent) && containsAny(
                nodes,
                "by signing up, you agree to",
                "bang viec dang ky, ban dong y voi dieu khoan",
            )
        ) return "IG_TERMS_CONSENT"
        if (has(nodes, contactInput)) return "IG_CONTACT_ENTRY"
        if (has(nodes, birthdayInput)) return "IG_BIRTHDAY_ENTRY"
        if (has(nodes, usernameInput) || has(nodes, instagramNameInput) || has(nodes, instagramBioInput)) {
            return "IG_PROFILE_SETUP"
        }
        // Luật dựa trên VĂN BẢN phải chạy SAU các phép kiểm ô nhập cụ thể.
        //
        // Màn "Tạo tên người dùng" có câu mô tả "Để bắt đầu tạo tài khoản, bạn
        // cần thêm tên người dùng..." -- trúng từ khoá "tạo tài khoản" ở dưới.
        // Khi luật này chạy trước, màn hình đó bị xếp vào IG_FINAL_SIGNUP_SUBMIT,
        // mà đó là màn hình được bảo vệ, nên runner dừng và đòi người thao tác
        // trong khi thực chất nó chỉ cần điền tên rồi bấm Tiếp. Cả luồng tạo tài
        // khoản đứng lại ở đây.
        //
        // Một ô nhập nhận diện được luôn là tín hiệu chắc chắn hơn một cụm từ
        // nằm trong đoạn văn mô tả.
        if (containsAny(nodes, "create account", "sign up", "đăng ký", "tạo tài khoản") &&
            !containsAny(nodes, "create new account", "tạo tài khoản mới")) return "IG_FINAL_SIGNUP_SUBMIT"
        if (containsAny(nodes, "add profile photo", "thêm ảnh đại diện")) return "IG_AVATAR_SETUP"
        if (hasHome(nodes, INSTAGRAM_PACKAGE)) return "IG_HOME"
        return "UNKNOWN"
    }

    private fun detectThreads(nodes: List<LocalUiNode>): String {
        if (bridge.foregroundPackage() != THREADS_PACKAGE) return "UNKNOWN"
        detectCommon(nodes)?.let { return it }
        if (has(nodes, threadsAddProfile)) return "THREADS_ACCOUNT_SWITCHER"
        if (has(nodes, threadsNameInput) || has(nodes, threadsBioInput)) return "THREADS_PROFILE_SETUP"
        if (has(nodes, threadsJoin) || has(nodes, continueSelector)) return "THREADS_ONBOARDING"
        if (hasHome(nodes, THREADS_PACKAGE)) return "THREADS_HOME"
        return "UNKNOWN"
    }

    /** Instagram đã từ chối đúng tên [username] này chưa. */
    private fun usernameRejected(nodes: List<LocalUiNode>, username: String): Boolean {
        val text = nodes.flatMap { listOf(it.text, it.contentDescription) }
            .joinToString(" ") { normalize(it) }
        // Bắt buộc thông báo phải nhắc chính tên đang điền, nếu không thì một
        // thông báo còn sót của tên khác sẽ chặn nhầm lượt điền hợp lệ.
        if (!text.contains(normalize(username))) return false
        return listOf(
            "khong dung duoc", "khong su dung duoc", "da co nguoi su dung", "da ton tai",
            "isn't available", "is not available", "not available", "is taken", "already taken",
        ).any(text::contains)
    }

    /** Đọc giá trị thật từ text của EditText; content-desc có thể giữ tên cũ. */
    private fun availableUsername(nodes: List<LocalUiNode>): String? {
        val input = nodes.firstOrNull { matches(it, usernameInput) } ?: return null
        val candidate = input.text.trim().lowercase(Locale.ROOT)
        if (!isInstagramUsername(candidate)) return null
        val markers = setOf(
            "username is available",
            "valid username",
            "ten nguoi dung hop le",
            "gia tri nhap la ten nguoi dung hop le",
        )
        val available = nodes.any { node ->
            listOf(node.text, node.contentDescription)
                .map(::normalize)
                .any { value -> markers.any(value::contains) }
        }
        return candidate.takeIf { available }
    }

    /**
     * Chọn gợi ý đầu tiên chỉ trên đúng màn username-rejected đã xác minh.
     * Instagram Compose hiện vẽ ba gợi ý nhưng không expose chúng trong cây
     * accessibility trên Redmi 9A. Khi đó dùng điểm ngay dưới thông báo lỗi;
     * vòng sau vẫn phải đọc được username hợp lệ trước khi bấm Tiếp.
     */
    private fun chooseUsernameSuggestion(nodes: List<LocalUiNode>, wantedUsername: String): Boolean {
        val exposed = nodes.firstOrNull { node ->
            node.clickable && !node.editable &&
                isInstagramUsername(node.text.trim()) &&
                usernameStem(node.text) == usernameStem(wantedUsername) &&
                !node.text.trim().equals(wantedUsername, ignoreCase = true)
        }
        if (exposed != null) {
            return bridge.click(
                LocalUiSelector(texts = setOf(exposed.text), requireClickable = true),
            )
        }

        val input = nodes.firstOrNull { matches(it, usernameInput) } ?: return false
        val rejection = nodes.firstOrNull { node ->
            val value = normalize(node.text + " " + node.contentDescription)
            value.contains(normalize(wantedUsername)) &&
                listOf("khong dung duoc", "khong su dung duoc", "not available", "is taken")
                    .any(value::contains)
        } ?: return false
        if (rejection.right <= rejection.left || rejection.bottom <= rejection.top) return false
        val x = (rejection.left + rejection.right) / 2
        val inputHeight = (input.bottom - input.top).coerceAtLeast(0)
        val y = rejection.bottom + maxOf(48, inputHeight)
        val screenBottom = nodes.maxOfOrNull { it.bottom } ?: 0
        if (x <= 0 || y <= rejection.bottom || (screenBottom > 0 && y >= screenBottom)) return false
        return bridge.tapAt(x, y)
    }

    private fun isInstagramUsername(value: String): Boolean =
        value.length in 1..30 && value.matches(Regex("[A-Za-z0-9._]+"))

    private fun usernameStem(value: String): String =
        value.lowercase(Locale.ROOT).replace(Regex("[._]"), "")

    private fun detectCommon(nodes: List<LocalUiNode>): String? {
        if (nodes.any { it.password }) return "PASSWORD_REQUIRED"
        val text = nodes.flatMap { listOf(it.text, it.contentDescription) }.joinToString(" ") { normalize(it) }
        return when {
            listOf("captcha", "i'm not a robot", "verify you're human", "xac minh ban la con nguoi").any(text::contains) -> "CAPTCHA_REQUIRED"
            listOf("verification code", "security code", "ma xac minh", "ma bao mat", "otp").any(text::contains) -> "OTP_REQUIRED"
            listOf("confirm your identity", "selfie", "xac minh danh tinh").any(text::contains) -> "SELFIE_OR_IDENTITY_CHECK"
            listOf("security challenge", "security check", "kiem tra bao mat").any(text::contains) -> "SECURITY_CHALLENGE"
            listOf("recover account", "account recovery", "khoi phuc tai khoan").any(text::contains) -> "ACCOUNT_RECOVERY"
            listOf("account disabled", "tai khoan bi vo hieu hoa").any(text::contains) -> "ACCOUNT_DISABLED"
            listOf("try again later", "rate limit", "thu lai sau").any(text::contains) -> "RATE_LIMITED"
            listOf("action blocked", "hanh dong bi chan").any(text::contains) -> "ACTION_BLOCKED"
            listOf("no internet", "network error", "khong co ket noi").any(text::contains) -> "NETWORK_ERROR"
            listOf("password", "mat khau").any(text::contains) -> "PASSWORD_REQUIRED"
            else -> null
        }
    }

    private fun hasHome(nodes: List<LocalUiNode>, packageName: String): Boolean {
        val prefix = "$packageName:id/"
        val home = nodes.any {
            normalize(it.contentDescription) in setOf("home", "trang chu", "feed", "bang feed") ||
                it.viewId in setOf(
                    "${prefix}feed_tab",
                    "${prefix}home_tab",
                    "${prefix}barcelona_tab_main_feed",
                    "barcelona_tab_main_feed",
                )
        }
        val profile = nodes.any {
            normalize(it.contentDescription) in setOf("profile", "trang ca nhan") ||
                it.viewId in setOf(
                    "${prefix}profile_tab",
                    "${prefix}barcelona_tab_profile",
                    "barcelona_tab_profile",
                )
        }
        return home && profile
    }

    private fun protectedOutcome(screen: String): LocalFlowOutcome? =
        if (screen in PROTECTED_SCREENS) {
            LocalFlowOutcome("waiting_human", screen, "HUMAN_VERIFICATION_REQUIRED")
        } else null

    private fun act(screen: String, ok: Boolean) =
        if (ok) LocalFlowOutcome("running", screen) else confirmation(screen)

    private fun confirmation(screen: String, reason: String = "UI_CHANGED") =
        LocalFlowOutcome("needs_confirmation", screen, reason)

    private fun containsAny(nodes: List<LocalUiNode>, vararg values: String): Boolean {
        val candidates = nodes.flatMap { listOf(it.text, it.contentDescription) }.map(::normalize)
        return values.map(::normalize).any { wanted -> candidates.any { it.contains(wanted) } }
    }

    private fun matches(node: LocalUiNode, selector: LocalUiSelector): Boolean = with(node) {
        (!selector.requireClickable || clickable) &&
            (!selector.requireLongClickable || longClickable) &&
            (!selector.requireEditable || editable) &&
            (viewId in selector.resourceIds || normalize(text) in selector.texts.map(::normalize) ||
                normalize(contentDescription) in selector.contentDescriptions.map(::normalize) ||
                selector.contentDescriptionPrefixes.any { prefix ->
                    normalize(contentDescription).startsWith(normalize(prefix))
                })
    }

    private fun has(nodes: List<LocalUiNode>, selector: LocalUiSelector): Boolean =
        nodes.any { matches(it, selector) }

    companion object {
        const val INSTAGRAM_PACKAGE = "com.instagram.android"
        const val THREADS_PACKAGE = "com.instagram.barcelona"
        private val PROTECTED_SCREENS = setOf(
            "PASSWORD_REQUIRED", "OTP_REQUIRED", "CAPTCHA_REQUIRED", "IG_FINAL_SIGNUP_SUBMIT",
            "SELFIE_OR_IDENTITY_CHECK", "SECURITY_CHALLENGE", "ACCOUNT_RECOVERY",
        )

        // NFD chỉ tách được dấu phụ (ô, ù, ê...). Chữ "đ" là một ký tự riêng,
        // không phải "d" cộng dấu, nên nó sống sót qua bước bỏ dấu: "được" ra
        // "đuoc" chứ không phải "duoc". Mọi mẫu ASCII chứa đ vì thế không bao
        // giờ khớp. Phía Python (core/niche.py) đã xử lý; bản Kotlin thì chưa.
        fun normalize(value: String): String = Normalizer.normalize(value, Normalizer.Form.NFD)
            .replace(Regex("\\p{Mn}+"), "")
            .replace("đ", "d")
            .replace("Đ", "D")
            .trim()
            .lowercase(Locale.ROOT)
    }
}
