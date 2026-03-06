"use strict";

(function () {
    const STORAGE_TICKET_KEY = "portal_bridge_ticket";
    const STORAGE_EXPIRES_KEY = "portal_bridge_expires_at";
    const DEFAULT_TTL_SECONDS = 15 * 60;

    let bridgeTicket = "";
    let bridgePollTimer = null;

    function getMsgBox() {
        return document.getElementById("msgBox");
    }

    function showMessage(text, type) {
        const msg = getMsgBox();
        if (!msg) return;
        msg.className = type === "error" ? "message error" : "message success";
        msg.textContent = text;
        msg.style.display = "block";
    }

    function ensureBridgeButton() {
        const loginBtn = document.getElementById("loginBtn");
        if (!loginBtn || document.getElementById("bridgeLoginBtn")) return;

        const btn = document.createElement("button");
        btn.type = "button";
        btn.id = "bridgeLoginBtn";
        btn.className = "btn";
        btn.style.display = "none";
        btn.textContent = "I already verified, click to login";
        btn.addEventListener("click", bridgeLogin);

        loginBtn.insertAdjacentElement("afterend", btn);
    }

    function setBridgeButtonVisible(visible) {
        const btn = document.getElementById("bridgeLoginBtn");
        if (!btn) return;
        btn.style.display = visible ? "block" : "none";
    }

    function persistBridgeTicket(ticket, expiresIn) {
        if (!ticket) return;
        bridgeTicket = ticket;
        const ttl = Number(expiresIn || 0) || DEFAULT_TTL_SECONDS;
        const expiresAt = Date.now() + ttl * 1000;
        sessionStorage.setItem(STORAGE_TICKET_KEY, bridgeTicket);
        sessionStorage.setItem(STORAGE_EXPIRES_KEY, String(expiresAt));
    }

    function clearBridgeTicket() {
        bridgeTicket = "";
        sessionStorage.removeItem(STORAGE_TICKET_KEY);
        sessionStorage.removeItem(STORAGE_EXPIRES_KEY);
    }

    function loadBridgeTicket() {
        const ticket = (sessionStorage.getItem(STORAGE_TICKET_KEY) || "").trim();
        if (!ticket) return "";
        const expiresAt = Number(sessionStorage.getItem(STORAGE_EXPIRES_KEY) || "0");
        if (expiresAt && Date.now() > expiresAt) {
            clearBridgeTicket();
            return "";
        }
        bridgeTicket = ticket;
        return bridgeTicket;
    }

    function stopBridgePolling() {
        if (bridgePollTimer) {
            clearInterval(bridgePollTimer);
            bridgePollTimer = null;
        }
    }

    async function pollBridgeStatus() {
        if (!bridgeTicket) return;
        try {
            const resp = await fetch(`/api/subscribe/bridge/${encodeURIComponent(bridgeTicket)}/status`);
            const data = await resp.json();
            if (!data.success) return;

            const status = data.data || {};
            if (status.expired) {
                stopBridgePolling();
                clearBridgeTicket();
                setBridgeButtonVisible(false);
                return;
            }
            if (status.verified) {
                stopBridgePolling();
                setBridgeButtonVisible(true);
                showMessage("Verification complete. Click the button below to login.", "success");
            }
        } catch (_) {
            // next polling tick will retry
        }
    }

    function startBridgePolling(ticket, expiresIn) {
        if (!ticket) return;
        persistBridgeTicket(ticket, expiresIn);
        setBridgeButtonVisible(false);
        stopBridgePolling();
        pollBridgeStatus();
        bridgePollTimer = setInterval(pollBridgeStatus, 2000);
    }

    async function bridgeLogin() {
        if (!bridgeTicket) return;
        const btn = document.getElementById("bridgeLoginBtn");
        if (!btn) return;

        btn.disabled = true;
        btn.textContent = "Logging in...";
        try {
            const resp = await fetch(`/api/subscribe/bridge/${encodeURIComponent(bridgeTicket)}/claim`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
            });
            const data = await resp.json();
            if (!data.success) {
                showMessage(data.error || "Login failed. Please request a new login link.", "error");
                return;
            }

            clearBridgeTicket();
            stopBridgePolling();
            const url = data.data && data.data.portal_url ? data.data.portal_url : "/portal?login=ok";
            window.location.href = url;
        } catch (_) {
            showMessage("Network error. Please retry.", "error");
        } finally {
            btn.disabled = false;
            btn.textContent = "I already verified, click to login";
        }
    }

    function pathOf(input) {
        try {
            if (typeof input === "string") return new URL(input, window.location.origin).pathname;
            if (input && typeof input.url === "string") return new URL(input.url, window.location.origin).pathname;
        } catch (_) {
            // ignore
        }
        return "";
    }

    function installFetchInterceptor() {
        if (window.__subscribeBridgeFetchPatched) return;
        window.__subscribeBridgeFetchPatched = true;

        const rawFetch = window.fetch.bind(window);
        window.fetch = async function (input, init) {
            const resp = await rawFetch(input, init);

            try {
                const path = pathOf(input);
                const method = ((init && init.method) || "GET").toUpperCase();
                if (
                    method === "POST" &&
                    (path === "/api/subscribe" || path === "/api/login/request")
                ) {
                    const data = await resp.clone().json().catch(() => null);
                    if (data && data.success && data.bridge_ticket) {
                        startBridgePolling(data.bridge_ticket, data.bridge_expires_in || DEFAULT_TTL_SECONDS);
                    }
                }
            } catch (_) {
                // no-op
            }

            return resp;
        };
    }

    document.addEventListener("DOMContentLoaded", function () {
        ensureBridgeButton();
        installFetchInterceptor();

        const restored = loadBridgeTicket();
        if (restored) {
            startBridgePolling(restored, 0);
        }
    });

    window.bridgeLogin = bridgeLogin;
})();
