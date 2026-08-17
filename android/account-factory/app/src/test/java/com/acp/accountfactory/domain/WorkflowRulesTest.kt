package com.acp.accountfactory.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WorkflowRulesTest {
    @Test
    fun happyPathRequiresEveryOnboardingStage() {
        assertTrue(Workflow.canTransition(AccountStage.PLANNED, AccountStage.IG_CREATED))
        assertTrue(Workflow.canTransition(AccountStage.IG_CREATED, AccountStage.THREADS_CREATED))
        assertTrue(Workflow.canTransition(AccountStage.THREADS_CREATED, AccountStage.ACP_CONNECTING))
        assertTrue(Workflow.canTransition(AccountStage.ACP_CONNECTING, AccountStage.ACP_ACTIVE))
    }

    @Test
    fun cannotSkipDirectlyToAcpActive() {
        assertFalse(Workflow.canTransition(AccountStage.PLANNED, AccountStage.ACP_ACTIVE))
        assertFalse(Workflow.canTransition(AccountStage.IG_CREATED, AccountStage.ACP_ACTIVE))
        assertFalse(Workflow.canTransition(AccountStage.THREADS_CREATED, AccountStage.ACP_ACTIVE))
    }

    @Test
    fun fiftyAccountsAreUniqueAndSplitIntoTenGroupsOfFive() {
        val rows = BatchGenerator.generate(count = 50, prefix = "acp")
        assertEquals(50, rows.size)
        assertEquals(50, rows.map { it.username }.toSet().size)
        assertEquals(10, rows.map { it.groupNo }.toSet().size)
        assertEquals(listOf(5,5,5,5,5,5,5,5,5,5), rows.groupingBy { it.groupNo }.eachCount().toSortedMap().values.toList())
    }
}
