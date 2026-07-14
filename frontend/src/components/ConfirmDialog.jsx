import React, { useState } from "react";
import Icon from "./Icon.jsx";
import Modal from "./Modal.jsx";

// Dialog xác nhận dùng chung cho hành động phá hủy (xóa lô/record) — thay
// window.confirm() thô. `confirmText` (tùy chọn): nếu truyền, người dùng phải
// gõ ĐÚNG chuỗi đó mới bật được nút Xóa — dành cho hậu quả lớn (xóa cả lô
// nhiều file); bỏ trống thì chỉ cần 1 lần bấm xác nhận (hậu quả nhỏ, dễ sửa
// lại bằng cách upload lại 1 file).
export default function ConfirmDialog({
  title, message, confirmLabel = "Xóa", confirmText, busy, blockedReason, onConfirm, onCancel,
}) {
  const [typed, setTyped] = useState("");
  const needsTyped = !!confirmText;
  const canConfirm = !busy && !blockedReason && (!needsTyped || typed === confirmText);

  return (
    <Modal title={title} onClose={onCancel}>
      <div className="confirm-dialog">
        <div className="confirm-icon" aria-hidden="true">
          <Icon name="alertTriangle" size={22} />
        </div>
        <div className="confirm-body">
          <div className="confirm-message">{message}</div>

          {blockedReason && (
            <div className="confirm-blocked">
              <Icon name="ban" size={14} /> {blockedReason}
            </div>
          )}

          {needsTyped && !blockedReason && (
            <div className="confirm-type-check">
              <label className="field-label" htmlFor="confirm-typed">
                Gõ lại <b>{confirmText}</b> để xác nhận
              </label>
              <input id="confirm-typed" className="text-input" value={typed} autoFocus
                autoComplete="off" spellCheck={false}
                onChange={(e) => setTyped(e.target.value)} />
            </div>
          )}
        </div>

        <div className="confirm-actions">
          <button type="button" className="ghost sm" disabled={busy} onClick={onCancel}>Hủy</button>
          <button type="button" className="danger sm" disabled={!canConfirm} onClick={onConfirm}>
            <Icon name="trash" size={14} /> {busy ? "Đang xóa…" : confirmLabel}
          </button>
        </div>
      </div>
    </Modal>
  );
}
