"use strict";

(function () {
    const EMAIL_KEY = "portal_persistent_email";

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

    function savePersistentLogin(email) {
        if (!email) return;
        localStorage.setItem(EMAIL_KEY, email);
    }

    function clearPersistentLogin() {
        localStorage.removeItem(EMAIL_KEY);
    }

    function getStoredLogin() {
        const email = (localStorage.getItem(EMAIL_KEY) || "").trim();
        return email || null;
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
                        savePersistentLogin(data.data.email);
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

    window.__portalPersistentLogin = {
        save: savePersistentLogin,
        clear: clearPersistentLogin,
    };
})();
