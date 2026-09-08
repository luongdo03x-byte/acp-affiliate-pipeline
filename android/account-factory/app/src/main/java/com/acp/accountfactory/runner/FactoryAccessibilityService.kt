package com.acp.accountfactory.runner

import android.accessibilityservice.AccessibilityService
import android.os.Bundle
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

class FactoryAccessibilityService : AccessibilityService(), LocalAccessibilityBridge {
    override fun onServiceConnected() {
        instance = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
        if (event.eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED &&
            event.eventType != AccessibilityEvent.TYPE_WINDOWS_CHANGED
        ) return

        observationStore.update(
            packageName = event.packageName?.toString(),
            className = event.className?.toString(),
            observedAtEpochMs = System.currentTimeMillis(),
        )
    }

    override fun onInterrupt() = Unit

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }

    override fun foregroundPackage(): String? =
        rootInActiveWindow?.packageName?.toString() ?: observationStore.latest().packageName

    override fun nodes(): List<LocalUiNode> {
        val root = rootInActiveWindow ?: return emptyList()
        return buildList { collect(root, this) }
    }

    override fun click(selector: LocalUiSelector): Boolean = withMatchingNode(selector) { node ->
        node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
    }

    override fun longClick(selector: LocalUiSelector): Boolean = withMatchingNode(selector) { node ->
        node.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK)
    }

    override fun setText(selector: LocalUiSelector, value: String): Boolean {
        if (value.isBlank() || value.length > 500 || value.any { it.code < 32 }) return false
        return withMatchingNode(selector) { node ->
            val arguments = Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value)
            }
            node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)
        }
    }

    private fun collect(node: AccessibilityNodeInfo, output: MutableList<LocalUiNode>) {
        val password = node.isPassword
        output += LocalUiNode(
            text = if (password) "" else node.text?.toString().orEmpty(),
            contentDescription = if (password) "" else node.contentDescription?.toString().orEmpty(),
            viewId = node.viewIdResourceName.orEmpty(),
            className = node.className?.toString().orEmpty(),
            clickable = node.isClickable,
            longClickable = node.isLongClickable,
            editable = node.isEditable,
            password = password,
        )
        for (index in 0 until node.childCount) {
            node.getChild(index)?.let { child ->
                try {
                    collect(child, output)
                } finally {
                    child.recycle()
                }
            }
        }
    }

    private fun withMatchingNode(
        selector: LocalUiSelector,
        action: (AccessibilityNodeInfo) -> Boolean,
    ): Boolean {
        val root = rootInActiveWindow ?: return false
        val node = find(root, selector) ?: return false
        return try {
            action(node)
        } finally {
            if (node !== root) node.recycle()
        }
    }

    private fun find(node: AccessibilityNodeInfo, selector: LocalUiSelector): AccessibilityNodeInfo? {
        if (matches(node, selector)) return node
        for (index in 0 until node.childCount) {
            val child = node.getChild(index) ?: continue
            val found = find(child, selector)
            if (found != null) {
                if (found !== child) child.recycle()
                return found
            }
            child.recycle()
        }
        return null
    }

    private fun matches(node: AccessibilityNodeInfo, selector: LocalUiSelector): Boolean {
        if (selector.requireClickable && !node.isClickable) return false
        if (selector.requireLongClickable && !node.isLongClickable) return false
        if (selector.requireEditable && !node.isEditable) return false
        val idMatch = node.viewIdResourceName.orEmpty() in selector.resourceIds
        val text = LocalSafeUiAutomation.normalize(node.text?.toString().orEmpty())
        val description = LocalSafeUiAutomation.normalize(node.contentDescription?.toString().orEmpty())
        return idMatch || selector.texts.any { LocalSafeUiAutomation.normalize(it) == text } ||
            selector.contentDescriptions.any { LocalSafeUiAutomation.normalize(it) == description }
    }

    private val AccessibilityNodeInfo.isEditable: Boolean
        get() = className?.toString()?.endsWith("EditText") == true ||
            actionList.any { it.id == AccessibilityNodeInfo.ACTION_SET_TEXT }

    companion object {
        val observationStore = ForegroundObservationStore()
        @Volatile
        var instance: FactoryAccessibilityService? = null
            private set
    }
}
