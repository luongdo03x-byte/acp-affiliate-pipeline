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
    fun setText(selector: LocalUiSelector, value: String): Boolean
}

data class LocalFlowOutcome(
    val status: String,
    val screen: String,
    val reason: String? = null,
) {
    fun result(): Map<String, Any?> = mapOf(
        "flow_status" to status,
        "screen" to screen,
        "reason" to reason,
    )
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
        resourceIds = setOf("com.instagram.barcelona:id/profile_tab"),
        contentDescriptions = setOf("Profile", "Trang cá nhân"),
        requireLongClickable = true,
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
            normalize(it.contentDescription) in setOf("home", "trang chu") ||
                it.viewId in setOf("${prefix}feed_tab", "${prefix}home_tab")
        }
        val profile = nodes.any {
            normalize(it.contentDescription) in setOf("profile", "trang ca nhan") ||
                it.viewId == "${prefix}profile_tab"
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

    private fun has(nodes: List<LocalUiNode>, selector: LocalUiSelector): Boolean = nodes.any {
        (!selector.requireClickable || it.clickable) &&
            (!selector.requireLongClickable || it.longClickable) &&
            (!selector.requireEditable || it.editable) &&
            (it.viewId in selector.resourceIds || normalize(it.text) in selector.texts.map(::normalize) ||
                normalize(it.contentDescription) in selector.contentDescriptions.map(::normalize) ||
                selector.contentDescriptionPrefixes.any { prefix ->
                    normalize(it.contentDescription).startsWith(normalize(prefix))
                })
    }

    companion object {
        const val INSTAGRAM_PACKAGE = "com.instagram.android"
        const val THREADS_PACKAGE = "com.instagram.barcelona"
        private val PROTECTED_SCREENS = setOf(
            "PASSWORD_REQUIRED", "OTP_REQUIRED", "CAPTCHA_REQUIRED", "IG_FINAL_SIGNUP_SUBMIT",
            "SELFIE_OR_IDENTITY_CHECK", "SECURITY_CHALLENGE", "ACCOUNT_RECOVERY",
        )

        fun normalize(value: String): String = Normalizer.normalize(value, Normalizer.Form.NFD)
            .replace(Regex("\\p{Mn}+"), "")
            .trim()
            .lowercase(Locale.ROOT)
    }
}
