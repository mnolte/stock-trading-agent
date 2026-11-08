/**
 * Loading timings for item 055 (design/DESIGN.md → Loading,
 * design/screens/Loading.dc.html). These mirror the tokens in
 * styles/tokens.css — they are frontend timings, not trading thresholds,
 * so they live here and not in config.example.yaml.
 */

/** No skeleton for answers faster than this. */
export const SKELETON_DELAY_MS = 200;
/** Once shown, a skeleton stays at least this long. */
export const SKELETON_MIN_MS = 400;
/** Refresh bar on a card that already shows data. */
export const REFRESH_DELAY_MS = 400;
/** "Still loading · n s" under a skeleton. */
export const LOAD_SLOW_MS = 8_000;
/** First load: stop waiting, show the error state. */
export const LOAD_TIMEOUT_MS = 20_000;
/** Buttons: the muted "waiting for …" line. */
export const BUTTON_WAIT_MS = 10_000;
/** Buttons: "No answer" — the client stops waiting. */
export const ACTION_TIMEOUT_MS = 20_000;
/** Accent tint on a row that arrived live. */
export const NEW_ROW_MS = 1_600;
/** Kill switch: an unanswered press is sent again this often. */
export const KILL_RESEND_MS = 5_000;
/** Kill switch: critical banner after this long without an answer. */
export const KILL_BANNER_MS = 30_000;
/** Confirmation toasts auto-close. */
export const TOAST_AUTO_CLOSE_MS = 10_000;
