// @ts-check
/* ============================================================================
 * Hex AI Girlfriend — 本地实时数字人语音对话 (Realtime AI Digital Girlfriend)
 * Copyright (C) 2026 Hex-电脑课堂 (Hex Computer Classroom)
 * YouTube: https://www.youtube.com/channel/UC2s3nBn_v4So9-8GQORMDhA
 * SPDX-License-Identifier: GPL-3.0-or-later
 * ========================================================================== */
/** Small shared helpers used across the UI modules: a strict query selector,
 *  HTML escaping for text we drop into innerHTML, error-string trimming, and
 *  the opt-in debug flag. */

/** Opt-in tracing: `localStorage.setItem("s2s.debug", "1")` then reload. */
export const DEBUG = (() => {
  try {
    return localStorage.getItem("s2s.debug") === "1";
  } catch {
    return false;
  }
})();

/**
 * Query a single element, throwing if it's missing (so a broken selector fails
 * loudly at startup rather than as a later null-deref).
 * @template {HTMLElement} T
 * @param {string} selector
 * @returns {T}
 */
export function $(selector) {
  const el = document.querySelector(selector);
  if (!el) throw new Error(`Missing element: ${selector}`);
  return /** @type {T} */ (el);
}

/** @param {string} s @returns {string} */
export function escHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** Trim a long error message to fit the orb caption. @param {string} text */
export function truncateError(text) {
  if (text.length <= 90) return text;
  return text.slice(0, 87) + "…";
}
