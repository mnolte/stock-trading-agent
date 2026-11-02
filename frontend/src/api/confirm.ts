/**
 * "Confirm it is you" plumbing (PR 23), kept out of auth.ts so
 * client.ts can import it statically: auth.ts imports get/post from
 * client.ts, so a client.ts → auth.ts edge would be a cycle.
 *
 * The confirm flow is requester-agnostic: `client.ts` calls
 * `requestConfirmation()` when a write answers 403 confirm_required,
 * Layout renders the dialog and the original request retries once.
 */

export interface ConfirmRequest {
  /** The risky action name, e.g. "Stop & flatten". */
  action: string;
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
export function requestConfirmation(action: string): Promise<boolean> {
  return new Promise((resolve) => {
    if (!confirmHandler) {
      resolve(false);
      return;
    }
    confirmHandler({ action, resolve });
  });
}
