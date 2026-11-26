---
item: 114
type: fix
title: WebSocket close race
status: merged
issue: 341
kickoff: —
spec_prs: []
build_prs: [342]
---

# 114 · fix · WebSocket close race

## 2026-11-26 · build PR #342

Found through the intermittent image-suite failure in `test_socket_cap_closes_the_oldest`: a registry-initiated close sent the close frame but left the feed loop subscribed until its next poll. An update published in that gap — in the failure, the 15-minute VIX retry's `settings`/`banners` update — made the loop send on the closed socket and fail the websocket task.

- `AuthService` now stores a close event with every registered socket and sets it before a sign-out, device revocation or socket-cap close. The feed loop waits on that event, so it returns and unsubscribes at once instead of after the next poll.
- A frame send that still loses the close race is treated as an already-closed socket: the loop exits instead of surfacing `WebSocketDisconnected` as a task failure.
- `test_update_after_registry_close_does_not_fail_socket` publishes after a sign-out close; before the fix it reproduced the image-suite failure deterministically.
