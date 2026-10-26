import type { ReactNode } from 'react';

import { DialogButton, Modal } from './Modal';

/** Simple confirm dialog used on the dev-components page — delegates to
 * Modal so it gets the same Escape/focus/focus-return behaviour. */
export function Dialog({
  open,
  title,
  children,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Modal
      open={open}
      title={title}
      onClose={onCancel}
      footer={
        <>
          <DialogButton variant="cancel" onClick={onCancel}>
            Cancel
          </DialogButton>
          <DialogButton variant="danger" onClick={onConfirm}>
            Confirm
          </DialogButton>
        </>
      }
    >
      <div style={{ color: 'var(--sa-text-2)' }}>{children}</div>
    </Modal>
  );
}
