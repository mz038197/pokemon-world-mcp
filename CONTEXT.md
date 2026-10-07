# Pokemon World MCP

A classroom MCP service where a student's agent plays in a shared Pokemon world. The student presents a Classroom API Key issued by the router.

## Language

**Classroom API Key**:
The per-student credential this service accepts for one Class Session. The router issues it, and this service checks that issuance itself.
_Avoid_: Personal API Key, a secret resolved by reading the router's key table

**Personal API Key**:
A long-lived teacher or admin key checked only by the router. This service does not accept it.
_Avoid_: Classroom API Key
