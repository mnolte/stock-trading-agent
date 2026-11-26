/**
 * "Confirm it is you" plumbing (PR 23), kept out of auth.ts so
 * client.ts can import it statically: auth.ts imports get/post from
 * client.ts, so a client.ts → auth.ts edge would be a cycle.
 *
 * The confirm flow is requester-agnostic: `client.ts` calls
 * `requestConfirmation()` when a write answers 403 confirm_required,
 * Layout renders the dialog and the original request retries once.
 * Item 082: the 403 may carry `changes` — the loosening edits that
 * need the confirmation — and the dialog lists them.
 */

export interface ConfirmChange {
  /** Dotted setting path, e.g. "decision.min_confidence_enter". */
  key: string;
  label: string;
  old: string;
  new: string;
}

export interface ConfirmRequest {
  /** The risky action name, e.g. "Stop & flatten". */
  action: string;
  /** The changes that need the confirmation (settings saves only). */
  changes?: ConfirmChange[];
  /** Called with the entered secret; resolves true when it confirmed. */
  resolve: (ok: boolean) => void;
}

type ConfirmHandler = (request: ConfirmRequest) => void;
let confirmHandler: ConfirmHandler | null = null;

export function registerConfirmHandler(handler: ConfirmHandler | null): void {
  confirmHandler = handler;
}

/**
 * Open the confirm dialog and resolve to whether it succeeded. When no
 * handler is mounted (SA_AUTH=off never produces confirm_required) it
 * resolves false.
 */
export function requestConfirmation(
  action: string,
  changes?: ConfirmChange[],
): Promise<boolean> {
  return new Promise((resolve) => {
    if (!confirmHandler) {
      resolve(false);
      return;
    }
    confirmHandler({ action, changes, resolve });
  });
}
