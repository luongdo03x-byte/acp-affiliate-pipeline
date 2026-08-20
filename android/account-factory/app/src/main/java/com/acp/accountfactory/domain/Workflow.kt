package com.acp.accountfactory.domain

enum class AccountStage { PLANNED, IG_CREATED, THREADS_CREATED, ACP_CONNECTING, ACP_ACTIVE, NEEDS_VERIFICATION, ERROR }

object Workflow {
    private val allowed = setOf(
        AccountStage.PLANNED to AccountStage.IG_CREATED,
        AccountStage.IG_CREATED to AccountStage.THREADS_CREATED,
        AccountStage.THREADS_CREATED to AccountStage.ACP_CONNECTING,
        AccountStage.ACP_CONNECTING to AccountStage.ACP_ACTIVE,
        AccountStage.PLANNED to AccountStage.NEEDS_VERIFICATION,
        AccountStage.IG_CREATED to AccountStage.NEEDS_VERIFICATION,
        AccountStage.THREADS_CREATED to AccountStage.NEEDS_VERIFICATION,
        AccountStage.ACP_CONNECTING to AccountStage.ERROR,
        AccountStage.NEEDS_VERIFICATION to AccountStage.PLANNED,
        AccountStage.NEEDS_VERIFICATION to AccountStage.IG_CREATED,
        AccountStage.ERROR to AccountStage.THREADS_CREATED,
    )

    fun canTransition(from: AccountStage, to: AccountStage) = (from to to) in allowed
}
