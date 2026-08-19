// 登入畫面 view:email/密碼註冊登入切換 + Google 登入連結。
// 在還沒登入時整頁蓋住 .wrap;登入/註冊成功呼叫 onSuccess()(由 app.js 傳入,
// 重跑一次開機流程),不自己碰其他 view 的狀態。
import {getAuthConfig, login, register} from "../api.js?v=20260820a";
import {state} from "../store.js?v=20260820a";
import {t} from "../i18n.js?v=20260820a";
import {$} from "../utils.js?v=20260820a";

let mode = "login";  // "login" | "register"
let onSuccessCb = null;

function render() {
  $("#authSubmit").textContent = mode === "login" ? t("auth.login") : t("auth.register");
  $("#authToggleMode").textContent = mode === "login" ? t("auth.toggleToRegister") : t("auth.toggleToLogin");
  $("#authPassword").autocomplete = mode === "login" ? "current-password" : "new-password";
}

function showError(msg) {
  const el = $("#authError");
  el.textContent = msg;
  el.style.display = msg ? "" : "none";
}

export function showAuthGate() {
  $("#authGate").style.display = "";
  // 依站台設定決定顯示哪些登入選項:註冊關閉時隱藏「註冊」切換,
  // Google 未設定時隱藏 Google 按鈕與分隔線(避免點了才拿到 500)。
  getAuthConfig().then(cfg => {
    $("#authToggleMode").style.display = cfg.registration_open ? "" : "none";
    if (!cfg.registration_open && mode === "register") { mode = "login"; render(); }
    $("#authGoogle").style.display = cfg.google_enabled ? "" : "none";
    const divider = document.querySelector("#authGate .auth-divider");
    if (divider) divider.style.display = cfg.google_enabled ? "" : "none";
  }).catch(() => {});
  // Google OAuth 導回時若被白名單擋下,用 query param 帶錯誤訊息回來
  const params = new URLSearchParams(location.search);
  if (params.get("error") === "not_whitelisted") {
    showError(t("auth.notWhitelisted"));
  } else if (params.get("error")) {
    showError(t("auth.failedRetry"));
  }
  if (params.has("error")) {
    history.replaceState(null, "", location.pathname);
  }
}

export function hideAuthGate() {
  $("#authGate").style.display = "none";
}

export function initAuth(onSuccess) {
  onSuccessCb = onSuccess;
  render();
  $("#authToggleMode").onclick = () => {
    mode = mode === "login" ? "register" : "login";
    showError("");
    render();
  };
  $("#authForm").addEventListener("submit", async e => {
    e.preventDefault();
    const email = $("#authEmail").value.trim();
    const password = $("#authPassword").value;
    if (!email || !password) return;
    showError("");
    const btn = $("#authSubmit");
    btn.disabled = true;
    try {
      // 帶著邀請 token 註冊會**繞過白名單與註冊模式**(後端 app/invites.py 的刻意設計):
      // 持有連結本身就是授權。登入不需要帶——已經有帳號的人由 boot() 自動接受。
      const r = mode === "login" ? await login(email, password)
        : await register(email, password, state.inviteToken);
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        showError(d.detail || (mode === "login" ? t("auth.loginFailed") : t("auth.registerFailed")));
        return;
      }
      // 系統裡第一個註冊的人自動成為 admin(見 app/routers/auth.py 的 first_user
      // 判斷),使用者往往不知道自己已經是管理者、更不知道帳密要好好保管——註冊
      // 成功回應帶 is_admin 時主動告知一次。
      if (mode === "register") {
        const d = await r.json().catch(() => ({}));
        if (d.is_admin) alert(t("auth.firstAdminWelcome"));
      }
      await onSuccessCb();
    } finally {
      btn.disabled = false;
    }
  });
}
