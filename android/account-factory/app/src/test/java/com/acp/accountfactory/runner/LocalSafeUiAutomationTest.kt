package com.acp.accountfactory.runner

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LocalSafeUiAutomationTest {
    private class FakeBridge(
        var packageName: String,
        var snapshot: List<LocalUiNode>,
    ) : LocalAccessibilityBridge {
        val clicks = mutableListOf<LocalUiSelector>()
        val taps = mutableListOf<Pair<Int, Int>>()
        val longClicks = mutableListOf<LocalUiSelector>()
        val values = mutableListOf<String>()
        override fun foregroundPackage() = packageName
        override fun nodes() = snapshot
        override fun click(selector: LocalUiSelector): Boolean {
            clicks += selector
            return true
        }
        override fun longClick(selector: LocalUiSelector): Boolean {
            longClicks += selector
            return true
        }
        override fun tapAt(x: Int, y: Int): Boolean {
            taps += x to y
            return true
        }
        override fun dismissKeyboard(): Boolean = true
        override fun setText(selector: LocalUiSelector, value: String): Boolean {
            values += value
            return true
        }
    }

    @Test
    fun normalizeCungPhaiBoChuD() {
        // NFD chỉ tách dấu phụ; "đ" là ký tự riêng nên sống sót qua bước bỏ dấu.
        // Thiếu bước này thì mọi mẫu ASCII chứa đ ("duoc", "dang ky"...) không
        // bao giờ khớp -- đúng lỗi đã làm bộ dò tên bị từ chối không hoạt động.
        assertEquals("duoc", LocalSafeUiAutomation.normalize("được"))
        assertEquals("dang ky", LocalSafeUiAutomation.normalize("Đăng ký"))
        assertEquals("khong dung duoc", LocalSafeUiAutomation.normalize("không dùng được"))
    }

    @Test
    fun rejectedUsernameSelectsFirstSuggestionInsteadOfRetypingForever() {
        // Instagram từ chối tên và ghi rõ tên đó trong thông báo. Trước đây runner
        // không nhận ra, cứ gõ lại mỗi vòng lặp -- mỗi lần gõ lại khởi động lại
        // quá trình kiểm tra tên, nên nó chạy vô hạn mà không bao giờ tiến được.
        val bridge = FakeBridge(
            LocalSafeUiAutomation.INSTAGRAM_PACKAGE,
            listOf(
                LocalUiNode(right = 720, bottom = 1449),
                LocalUiNode(text = "Tạo tên người dùng", contentDescription = "Tạo tên người dùng"),
                LocalUiNode(
                    text = "phuongthao.pham26",
                    contentDescription = "Tên người dùng,phuongthao.pham26",
                    className = "android.widget.EditText",
                    clickable = true,
                    editable = true,
                    left = 64,
                    top = 502,
                    right = 584,
                    bottom = 541,
                ),
                LocalUiNode(
                    text = "Tên người dùng phuongthao.pham26 không dùng được.",
                    contentDescription = "Tên người dùng phuongthao.pham26 không dùng được.",
                    left = 32,
                    top = 582,
                    right = 688,
                    bottom = 652,
                ),
                LocalUiNode(contentDescription = "Tiếp", className = "android.widget.Button", clickable = true),
            ),
        )

        val result = LocalSafeUiAutomation(bridge).runInstagram(mapOf("username" to "phuongthao.pham26"))

        assertEquals("USERNAME_SUGGESTION_SELECTED", result.reason)
        assertEquals("running", result.status)
        assertTrue("không được gõ lại tên đã bị từ chối", bridge.values.isEmpty())
        assertTrue("không được bấm Tiếp khi tên đã bị từ chối", bridge.clicks.isEmpty())
        assertEquals(listOf(360 to 700), bridge.taps)
    }

    @Test
    fun validSuggestedUsernameIsReturnedAndContinuedWithoutOverwriting() {
        val bridge = FakeBridge(
            LocalSafeUiAutomation.INSTAGRAM_PACKAGE,
            listOf(
                LocalUiNode(
                    text = "phuo.ngthaopham6",
                    contentDescription = "Tên người dùng,phuongthaopham6",
                    className = "android.widget.EditText",
                    clickable = true,
                    editable = true,
                ),
                LocalUiNode(
                    contentDescription = "Giá trị nhập là Tên người dùng hợp lệ.",
                ),
                LocalUiNode(contentDescription = "Tiếp", clickable = true),
            ),
        )

        val result = LocalSafeUiAutomation(bridge).runInstagram(
            mapOf("username" to "phuongthaopham6"),
        )

        assertEquals("running", result.status)
        assertEquals("phuo.ngthaopham6", result.actualUsername)
        assertEquals("phuo.ngthaopham6", result.result()["actual_username"])
        assertTrue("không được ghi đè gợi ý hợp lệ", bridge.values.isEmpty())
        assertEquals(1, bridge.clicks.size)
    }

    @Test
    fun validRequestedUsernameIsContinuedWithoutRestartingValidation() {
        val bridge = FakeBridge(
            LocalSafeUiAutomation.INSTAGRAM_PACKAGE,
            listOf(
                LocalUiNode(
                    text = "phuongthao.pham26",
                    contentDescription = "Tên người dùng,squirrel.27519677",
                    className = "android.widget.EditText",
                    clickable = true,
                    editable = true,
                ),
                LocalUiNode(
                    contentDescription = "Giá trị nhập là Tên người dùng hợp lệ.",
                ),
                LocalUiNode(contentDescription = "Tiếp", clickable = true),
            ),
        )

        val result = LocalSafeUiAutomation(bridge).runInstagram(
            mapOf("username" to "phuongthao.pham26"),
        )

        assertEquals("running", result.status)
        assertTrue("không được restart validation của tên đã hợp lệ", bridge.values.isEmpty())
        assertEquals(1, bridge.clicks.size)
    }

    @Test
    fun rejectionMessageForAnotherNameDoesNotBlockCurrentName() {
        // Thông báo còn sót của tên CŨ không được làm dừng lượt điền tên MỚI.
        val bridge = FakeBridge(
            LocalSafeUiAutomation.INSTAGRAM_PACKAGE,
            listOf(
                LocalUiNode(
                    text = "squirrel.27519677",
                    contentDescription = "Tên người dùng,squirrel.27519677",
                    className = "android.widget.EditText",
                    clickable = true,
                    editable = true,
                ),
                LocalUiNode(
                    text = "Tên người dùng phuongthaop không dùng được.",
                    contentDescription = "Tên người dùng phuongthaop không dùng được.",
                ),
                LocalUiNode(contentDescription = "Tiếp", className = "android.widget.Button", clickable = true),
            ),
        )

        val result = LocalSafeUiAutomation(bridge).runInstagram(mapOf("username" to "phuongthao.pham26"))

        assertTrue("phải điền tên mới", bridge.values.contains("phuongthao.pham26"))
        assertEquals(null, result.reason)
    }

    @Test
    fun usernameScreenIsNotMistakenForFinalSubmit() {
        // Cây UI thật chụp từ Redmi 9A. Instagram dựng màn này bằng Compose nên
        // MỌI node đều có resource-id rỗng; ô nhập chỉ nhận ra được qua
        // content-desc. Phần mô tả chứa cụm "tạo tài khoản", đúng từ khoá mà
        // luật IG_FINAL_SIGNUP_SUBMIT dò -- nên nếu luật đó chạy trước phép
        // kiểm ô nhập, màn hình bị coi nhầm là bước cần người và cả luồng dừng.
        val bridge = FakeBridge(
            LocalSafeUiAutomation.INSTAGRAM_PACKAGE,
            listOf(
                LocalUiNode(text = "Tạo tên người dùng", contentDescription = "Tạo tên người dùng"),
                LocalUiNode(
                    text = "Để bắt đầu tạo tài khoản, bạn cần thêm tên người dùng hoặc dùng gợi ý của chúng tôi.",
                    contentDescription = "Để bắt đầu tạo tài khoản, bạn cần thêm tên người dùng hoặc dùng gợi ý của chúng tôi.",
                ),
                LocalUiNode(
                    text = "squirrel.27519677",
                    contentDescription = "Tên người dùng,squirrel.27519677",
                    className = "android.widget.EditText",
                    clickable = true,
                    longClickable = true,
                    editable = true,
                ),
                LocalUiNode(contentDescription = "Tiếp", className = "android.widget.Button", clickable = true),
            ),
        )

        val result = LocalSafeUiAutomation(bridge).runInstagram(mapOf("username" to "phuongthaop"))

        assertEquals(
            "màn hình nhập tên người dùng bị coi nhầm là bước cần người",
            "IG_PROFILE_SETUP",
            result.screen,
        )
        assertTrue(
            "phải điền tên người dùng của hồ sơ, không dùng gợi ý của Instagram",
            bridge.values.contains("phuongthaop"),
        )
    }

    @Test
    fun passwordAlwaysStopsBeforeMutation() {
        val bridge = FakeBridge(
            LocalSafeUiAutomation.INSTAGRAM_PACKAGE,
            listOf(LocalUiNode(className = "android.widget.EditText", editable = true, password = true)),
        )

        val result = LocalSafeUiAutomation(bridge).runInstagram(mapOf("username" to "sample"))

        assertEquals("waiting_human", result.status)
        assertEquals("PASSWORD_REQUIRED", result.screen)
        assertTrue(bridge.clicks.isEmpty())
        assertTrue(bridge.values.isEmpty())
    }

    @Test
    fun knownInitialSignupCanBeTapped() {
        val bridge = FakeBridge(
            LocalSafeUiAutomation.INSTAGRAM_PACKAGE,
            listOf(LocalUiNode(contentDescription = "Tạo tài khoản mới", clickable = true)),
        )

        val result = LocalSafeUiAutomation(bridge).runInstagram(emptyMap())

        assertEquals("running", result.status)
        assertEquals("IG_SIGNUP_ENTRY", result.screen)
        assertEquals(1, bridge.clicks.size)
    }

    @Test
    fun finalSignupSubmitIsNeverTapped() {
        val bridge = FakeBridge(
            LocalSafeUiAutomation.INSTAGRAM_PACKAGE,
            listOf(LocalUiNode(text = "Đăng ký", clickable = true)),
        )

        val result = LocalSafeUiAutomation(bridge).runInstagram(emptyMap())

        assertEquals("waiting_human", result.status)
        assertEquals("IG_FINAL_SIGNUP_SUBMIT", result.screen)
        assertTrue(bridge.clicks.isEmpty())
    }

    @Test
    fun unknownUiNeverMutates() {
        val bridge = FakeBridge(
            LocalSafeUiAutomation.THREADS_PACKAGE,
            listOf(LocalUiNode(text = "Unexpected experiment")),
        )

        val result = LocalSafeUiAutomation(bridge).runThreads(emptyMap())

        assertEquals("needs_confirmation", result.status)
        assertFalse(bridge.clicks.isNotEmpty())
        assertTrue(bridge.values.isEmpty())
    }

    @Test
    fun existingInstagramHomeOpensAccountSwitcher() {
        val bridge = FakeBridge(
            LocalSafeUiAutomation.INSTAGRAM_PACKAGE,
            listOf(
                LocalUiNode(contentDescription = "Trang chủ"),
                LocalUiNode(
                    viewId = "com.instagram.android:id/profile_tab",
                    contentDescription = "Trang cá nhân",
                    longClickable = true,
                ),
            ),
        )

        val result = LocalSafeUiAutomation(bridge).runInstagram(mapOf("username" to "new_user"))

        assertEquals("running", result.status)
        assertEquals("IG_HOME", result.screen)
        assertTrue(bridge.clicks.isEmpty())
        assertEquals(1, bridge.longClicks.size)
    }

    @Test
    fun observingExistingThreadsHomeDoesNotAdvanceFlow() {
        val bridge = FakeBridge(
            LocalSafeUiAutomation.THREADS_PACKAGE,
            listOf(
                LocalUiNode(contentDescription = "Home"),
                LocalUiNode(contentDescription = "Profile"),
            ),
        )

        val result = LocalSafeUiAutomation(bridge).observe("threads")

        assertEquals("needs_confirmation", result.status)
        assertEquals("THREADS_HOME", result.screen)
        assertEquals("CHECKPOINT_NOT_CONFIRMED", result.reason)
        assertTrue(bridge.clicks.isEmpty())
    }

    @Test
    fun instagramAccountSwitcherAddsAnotherAccount() {
        val bridge = FakeBridge(
            LocalSafeUiAutomation.INSTAGRAM_PACKAGE,
            listOf(LocalUiNode(contentDescription = "Thêm tài khoản Instagram", clickable = true)),
        )

        val result = LocalSafeUiAutomation(bridge).runInstagram(emptyMap())

        assertEquals("running", result.status)
        assertEquals("IG_ACCOUNT_SWITCHER", result.screen)
        assertEquals(1, bridge.clicks.size)
        assertTrue(bridge.longClicks.isEmpty())
    }

    @Test
    fun usernameEntryWithoutResourceIdUsesAccessibilityLabelPrefix() {
        val bridge = FakeBridge(
            LocalSafeUiAutomation.INSTAGRAM_PACKAGE,
            listOf(
                LocalUiNode(text = "Tạo tên người dùng"),
                LocalUiNode(
                    contentDescription = "Tên người dùng,squirrel.83916366",
                    className = "android.widget.EditText",
                    clickable = true,
                    editable = true,
                ),
                LocalUiNode(contentDescription = "Tiếp", clickable = true),
            ),
        )

        val result = LocalSafeUiAutomation(bridge).runInstagram(
            mapOf("username" to "acp_generated_user"),
        )

        assertEquals("running", result.status)
        assertEquals("IG_PROFILE_SETUP", result.screen)
        assertEquals(listOf("acp_generated_user"), bridge.values)
        assertEquals(1, bridge.clicks.size)
    }
}
