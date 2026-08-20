package com.acp.accountfactory.domain

data class GeneratedAccount(val sequence: Int, val groupNo: Int, val username: String, val displayName: String, val bio: String)

object BatchGenerator {
    fun generate(count: Int, prefix: String): List<GeneratedAccount> {
        require(count in 1..500)
        val safe = prefix.lowercase().filter { it.isLetterOrDigit() }.ifBlank { "acp" }
        return (1..count).map { n ->
            GeneratedAccount(
                sequence = n,
                groupNo = ((n - 1) / 5) + 1,
                username = "$safe${n.toString().padStart(2, '0')}",
                displayName = "ACP Profile ${n.toString().padStart(2, '0')}",
                bio = "Curated finds • profile ${n.toString().padStart(2, '0')}",
            )
        }
    }
}
