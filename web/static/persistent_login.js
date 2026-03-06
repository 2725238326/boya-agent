"use strict";

(function () {
    const TOKEN_KEY = "portal_persistent_token";
    const EMAIL_KEY = "portal_persistent_email";
    const RESTORE_ATTEMPT_KEY = "portal_restore_attempted";

    function pathOf(input) {
        try {
            if (typeof input === "string") {
                return new URL(input, window.location.origin).pathname;
            }
            if (input && typeof input.url === "string") {
                return new URL(input.url, window.location.origin).pathname;
            }
        } catch (_) {
            return "";
        }
        return "";
    }

    function savePersistentLogin(email, token) {
        if (!email || !token) return;
        localStorage.setItem(TOKEN_KEY, token);
        localStorage.setItem(EMAIL_KEY, email);
        sessionStorage.removeItem(RESTORE_ATTEMPT_KEY);
    }

    function clearPersistentLogin() {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(EMAIL_KEY);
        sessionStorage.removeItem(RESTORE_ATTEMPT_KEY);
    }

    function getStoredLogin() {
        const token = (localStorage.getItem(TOKEN_KEY) || "").trim();
        const email = (localStorage.getItem(EMAIL_KEY) || "").trim();
        if (!token || !email) return null;
        return { token, email };
    }

    function maybeRestoreLogin() {
        const params = new URLSearchParams(window.location.search);
        if (window.location.pathname !== "/subscribe") return;
        if (params.has("force") || params.has("result")) return;

        const stored = getStoredLogin();
        if (!stored) return;

        if (sessionStorage.getItem(RESTORE_ATTEMPT_KEY) === "1") {
            clearPersistentLogin();
            return;
        }

        sessionStorage.setItem(RESTORE_ATTEMPT_KEY, "1");
        const target = `/portal?token=${encodeURIComponent(stored.token)}&email=${encodeURIComponent(stored.email)}`;
        window.location.replace(target);
    }

    function installFetchInterceptor() {
        if (window.__persistentLoginFetchPatched) return;
        window.__persistentLoginFetchPatched = true;

        const rawFetch = window.fetch.bind(window);
        window.fetch = async function (input, init) {
            const resp = await rawFetch(input, init);

            try {
                const path = pathOf(input);
                const method = ((init && init.method) || "GET").toUpperCase();

                if (method === "GET" && path === "/api/subscriber/session") {
                    const data = await resp.clone().json().catch(() => null);
                    if (data && data.success && data.data) {
                        savePersistentLogin(data.data.email, data.data.token);
                    }
                }

                if (
                    resp.ok &&
                    method === "POST" &&
                    (path === "/api/session/clear" || path === "/api/unsubscribe")
                ) {
                    clearPersistentLogin();
                }
            } catch (_) {
                // no-op
            }

            return resp;
        };
    }

    installFetchInterceptor();
    maybeRestoreLogin();

    window.__portalPersistentLogin = {
        save: savePersistentLogin,
        clear: clearPersistentLogin,
    };
})();
