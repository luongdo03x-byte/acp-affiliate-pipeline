package com.acp.accountfactory.domain

enum class AccountStage { PLANNED, IG_CREATED, THREADS_CREATED, ACP_CONNECTING, ACP_ACTIVE, NEEDS_VERIFICATION, ERROR }

object Workflow {
    private val allowed = setOf(
        PLANNED to IG_CREATED,
        IG_CREATED to THREADS_CREATED,
        THREADS_CREATED to ACP_CONNECTING,
        ACP_CONNECTING to ACP_ACTIVE,
        PLANNED to NEEDS_VERIFICATION,
        IG_CREATED to NEEDS_VERIFICATION,
        THREADS_CREATED to NEEDS_VERIFICATION,
        ACP_CONNECTING to ERROR,
        NEEDS_VERIFICATION to PLANNED,
        NEEDS_VERIFICATION to IG_CREATED,
        ERROR to THREADS_CREATED,
    )
    fun canTransition(from: AccountStage, to: AccountStage) = (from to to) in allowed
}
