# Pokemon World MCP

A classroom MCP service where a student's agent plays in a shared Pokemon world. The student presents a Classroom API Key issued by the router.

## Language

**Revocation List**:
This service's copy of the before-expiry refusals the router publishes. The service refreshes that copy from the router every 60 seconds. An entry names one key, one student, one Class Session, or one Class. A student call uses the copy. With no copy, the service refuses student calls. A copy older than 600 seconds is not used, and the service then refuses every student call. That bound is the same during a sitting.
_Avoid_: asking the router on each student call, a push from the router, reading the router's tables, a longer bound during a sitting

**Revocation List Credential**:
The one secret this service shares with vans-mcp-server to fetch the Revocation List and to ask the router to check a legacy Classroom API Key. The legacy key is the request body, not the secret. It is not a Classroom API Key or a Personal API Key.
_Avoid_: a student bearer, a public read, a database connection string

**Classroom API Key**:
The per-student credential this service accepts for one Class Session. The router issues it, and this service checks that issuance itself. It expires at the expiry the router fixed when it was issued. This service also refuses it when the student is disabled, when its Class is not active or past the Class end, when a newer key for that student in that sitting ended it, or while its sitting is closed. Opening that sitting accepts it again when its own expiry has not passed. Enabling the student again, or the Class being active with its end still ahead, accepts an unexpired key again without a new redeem. A key ended because a newer one was issued stays ended. The student sees a Key Refusal.
_Avoid_: Personal API Key, a secret resolved by reading the router's key table, the sitting's current expiry, a new redeem to restore a key after the sitting is opened, one shared 無效的 API 金鑰 for every cause

**Key Refusal**:
The notice this service shows when a Classroom API Key is refused before its expiry. A key ended by a newer one is 已在其他電腦兌換. A closed Class Session is 課堂已關閉. A disabled student is 學生已被停用. A Class that is not active or past its end is 課程已結束或停用. The key's own expiry remains「API 金鑰已過期，請至 Portal 重新取得邀請碼」. When more than one cause applies, the notice is the first that still blocks a new redeem: the disabled student, then the Class, then the closed sitting, then the key ended by a newer one.
_Avoid_: 無效的 API 金鑰 for every cause, a nickname or email on the Revocation List

**Personal API Key**:
A long-lived teacher or admin key checked only by the router. This service does not accept it.
_Avoid_: Classroom API Key
